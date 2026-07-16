import requests
import json
import os
import time
from datetime import datetime

# ===== CONFIGURAÇÕES (vem do GitHub Secrets) =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== CONFIGURAÇÕES DO FILTRO =====
# Pode ser alterado sem mexer no código: GitHub → Settings → Secrets and variables
# → Actions → aba "Variables" → crie/edite DESCONTO_MINIMO (ex: 90, 95, 99)
DESCONTO_MINIMO = int(os.getenv("DESCONTO_MINIMO", "95"))

# ===== ARQUIVO DE HISTÓRICO (para não repetir promoção) =====
HISTORICO_FILE = "historico.json"


def carregar_historico():
    if os.path.exists(HISTORICO_FILE):
        with open(HISTORICO_FILE, "r") as f:
            return set(json.load(f))
    return set()


def salvar_historico(historico):
    with open(HISTORICO_FILE, "w") as f:
        json.dump(list(historico), f)


def buscar_cotacao_dolar():
    """Busca a cotação atual do dólar em reais. Se falhar, usa um valor fixo de reserva."""
    try:
        response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10)
        response.raise_for_status()
        dados = response.json()
        return float(dados["rates"]["BRL"])
    except Exception as e:
        print(f"⚠️ Não foi possível buscar cotação do dólar ({e}). Usando valor de reserva.")
        return 5.50  # valor de reserva caso a API de cotação falhe


# ===================== FONTE 1: CheapShark (promoções pagas) =====================

def buscar_promocoes_cheapshark():
    url = "https://www.cheapshark.com/api/1.0/deals"
    params = {
        'storeID': 11,        # Xbox/Microsoft Store
        'sortBy': 'Savings',
        'pageSize': 60,
        'upperPrice': 15      # só jogos baratos (joias escondidas!)
    }
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def filtrar_promocoes_cheapshark(jogos, cotacao_dolar):
    print(f"🔎 CheapShark retornou {len(jogos)} itens brutos para a loja Xbox.")
    promocoes = []
    descartados_preco_zero = 0
    descartados_desconto = 0
    descartados_nota = 0
    for jogo in jogos:
        desconto = float(jogo.get('savings', 0))
        metacritic = int(jogo.get('metacriticScore') or 0)
        nota_usuarios = int(jogo.get('steamRatingPercent') or 0)
        preco_usd = float(jogo.get('salePrice', 0))

        if preco_usd <= 0:
            descartados_preco_zero += 1
            continue  # jogos grátis são tratados pela fonte GamerPower

        if desconto < DESCONTO_MINIMO:
            descartados_desconto += 1
            continue

        if not (metacritic >= 70 or nota_usuarios >= 80):
            descartados_nota += 1
            continue

        promocoes.append({
            'titulo': jogo['title'],
            'desconto': f"{desconto:.0f}%",
            'preco_brl': preco_usd * cotacao_dolar,
            'eh_gratis': False,
            'metacritic': metacritic,
            'nota_users': nota_usuarios,
            'imagem': jogo.get('thumb'),
            'link': f"https://www.cheapshark.com/redirect?dealID={jogo['dealID']}",
            'id': f"cs_{jogo['dealID']}"
        })

    print(f"   ↳ Descartados por preço zero (não é o alvo aqui): {descartados_preco_zero}")
    print(f"   ↳ Descartados por desconto abaixo de {DESCONTO_MINIMO}%: {descartados_desconto}")
    print(f"   ↳ Descartados por nota (Metacritic/Steam) insuficiente: {descartados_nota}")
    print(f"   ↳ Passaram no filtro: {len(promocoes)}")
    return promocoes


# ===================== FONTE 2: GamerPower (jogos grátis / giveaways) =====================

def buscar_gratis_gamerpower():
    """Busca giveaways de jogos grátis para Xbox (Series X/S e One)."""
    plataformas = ["xbox-series-xs", "xbox-one"]
    resultados = {}
    for plataforma in plataformas:
        try:
            url = "https://www.gamerpower.com/api/giveaways"
            params = {"platform": plataforma, "type": "game"}
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            dados = resp.json()

            if not isinstance(dados, list):
                # A API retorna um objeto (não uma lista) quando não há
                # giveaways ativos para essa plataforma no momento.
                print(f"⚪ Nenhum jogo grátis no momento para {plataforma}.")
                continue

            for item in dados:
                resultados[item['id']] = item  # dedup por id
        except Exception as e:
            print(f"⚠️ Erro ao buscar GamerPower ({plataforma}): {e}")
    return list(resultados.values())


def filtrar_gratis_gamerpower(giveaways):
    gratis = []
    for item in giveaways:
        gratis.append({
            'titulo': item.get('title', 'Jogo grátis'),
            'desconto': "100%",
            'preco_brl': 0.0,
            'eh_gratis': True,
            'metacritic': 0,
            'nota_users': 0,
            'imagem': item.get('image'),
            'link': item.get('open_giveaway_url') or item.get('gamerpower_url'),
            'id': f"gp_{item['id']}"
        })
    return gratis


# ===================== TELEGRAM =====================

def montar_legenda(j):
    if j['eh_gratis']:
        legenda = f"🎮 <b>{j['titulo']}</b>\n💚 <b>GRÁTIS</b>\n"
    else:
        legenda = f"🎮 <b>{j['titulo']}</b>\n💰 <b>{j['desconto']} OFF</b> — R$ {j['preco_brl']:.2f}\n"
    if j['metacritic'] > 0:
        legenda += f"⭐ Metacritic: {j['metacritic']}"
    if j['nota_users'] > 0:
        legenda += f" | Users: {j['nota_users']}%"
    if j['link']:
        legenda += f"\n🔗 <a href='{j['link']}'>Ver oferta</a>"
    return legenda


def enviar_texto_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    dados = {
        "chat_id": CHAT_ID,
        "text": mensagem,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    resp = requests.post(url, data=dados)
    resp.raise_for_status()


def enviar_foto_telegram(imagem_url, legenda):
    """Envia uma foto com legenda. Se a imagem falhar, envia como texto puro."""
    try:
        if not imagem_url:
            raise ValueError("sem imagem disponível")
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        dados = {
            "chat_id": CHAT_ID,
            "photo": imagem_url,
            "caption": legenda,
            "parse_mode": "HTML"
        }
        resp = requests.post(url, data=dados)
        resp.raise_for_status()
    except Exception as e:
        print(f"⚠️ Falha ao enviar imagem ({e}). Enviando como texto.")
        enviar_texto_telegram(legenda)


def main():
    print(f"🕐 Rodando em {datetime.now()}")

    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_TOKEN ou CHAT_ID não configurados nos Secrets.")
        return

    historico = carregar_historico()
    cotacao_dolar = buscar_cotacao_dolar()
    print(f"💵 Cotação atual: USD 1 = R$ {cotacao_dolar:.2f}")

    # Fonte 1: promoções pagas (CheapShark)
    jogos_cheapshark = buscar_promocoes_cheapshark()
    promocoes = filtrar_promocoes_cheapshark(jogos_cheapshark, cotacao_dolar)

    # Fonte 2: jogos grátis (GamerPower)
    giveaways = buscar_gratis_gamerpower()
    gratis = filtrar_gratis_gamerpower(giveaways)

    todas = promocoes + gratis

    # Filtra só as NOVAS (que ainda não foram enviadas)
    novas = [j for j in todas if j['id'] not in historico]

    if not novas:
        print("⚪ Nenhuma novidade hoje.")
        return

    novas_gratis = [j for j in novas if j['eh_gratis']]
    novas_promocoes = [j for j in novas if not j['eh_gratis']]

    # Mensagem de abertura (resumo)
    enviar_texto_telegram(f"🚨 <b>ALERTA: {len(novas)} JOIAS RARAS NO XBOX!</b>")

    if novas_gratis:
        enviar_texto_telegram("🆓 <b>JOGOS GRÁTIS</b>")
        for j in novas_gratis[:10]:
            enviar_foto_telegram(j['imagem'], montar_legenda(j))
            time.sleep(1)  # evita atingir limite de envio do Telegram

    if novas_promocoes:
        enviar_texto_telegram(f"💥 <b>PROMOÇÕES ({DESCONTO_MINIMO}%+ OFF)</b>")
        for j in novas_promocoes[:10]:
            enviar_foto_telegram(j['imagem'], montar_legenda(j))
            time.sleep(1)

    print(f"✅ {len(novas)} novidades enviadas!")

    # Atualiza histórico
    for j in novas:
        historico.add(j['id'])
    salvar_historico(historico)


if __name__ == "__main__":
    main()
