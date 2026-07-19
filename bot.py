import requests
import json
import os
import time
from datetime import datetime

# ===== CONFIGURAÇÕES (vem do GitHub Secrets) =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ITAD_API_KEY = os.getenv("ITAD_API_KEY")  # opcional: ativa a fonte oficial Microsoft Store

# ===== CONFIGURAÇÕES DO FILTRO =====
# Pode ser alterado sem mexer no código: GitHub → Settings → Secrets and variables
# → Actions → aba "Variables" → crie/edite DESCONTO_MINIMO (ex: 80, 85, 95)
DESCONTO_MINIMO = int(os.getenv("DESCONTO_MINIMO", "85"))

# Dia da semana para o resumo semanal (0=segunda, 1=terça, ..., 6=domingo)
DIA_RESUMO_SEMANAL = int(os.getenv("DIA_RESUMO_SEMANAL", "0"))
HISTORICO_FILE = "historico.json"

# Link para a página de Actions do repositório (usado nos alertas de "quebrou")
REPO = os.getenv("GITHUB_REPOSITORY", "")
LINK_ACTIONS = f"https://github.com/{REPO}/actions" if REPO else ""

# Lista de problemas detectados durante a execução (para o alerta de saúde)
alertas_saude = []


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
        alertas_saude.append("Cotação do dólar: falhou, usando valor de reserva (R$ 5,50)")
        return 5.50


# ===================== FONTE 1: CheapShark (promoções pagas) =====================

def buscar_promocoes_cheapshark():
    url = "https://www.cheapshark.com/api/1.0/deals"
    params = {
        'storeID': 11,
        'sortBy': 'Savings',
        'pageSize': 60,
        'upperPrice': 15
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        dados = response.json()
        if not isinstance(dados, list) or len(dados) == 0:
            alertas_saude.append("CheapShark: retornou 0 itens (pode estar fora do ar ou mudou de formato)")
        return dados
    except Exception as e:
        print(f"⚠️ Erro ao buscar CheapShark: {e}")
        alertas_saude.append(f"CheapShark: erro ao buscar dados ({e})")
        return []


_cache_genero_steam = {}


def buscar_genero_steam(steam_app_id):
    """Busca o(s) gênero(s) de um jogo na API pública da Steam, usando o steamAppID."""
    if not steam_app_id:
        return None
    if steam_app_id in _cache_genero_steam:
        return _cache_genero_steam[steam_app_id]
    try:
        url = "https://store.steampowered.com/api/appdetails"
        params = {"appids": steam_app_id, "l": "portuguese"}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        dados = resp.json()
        info = dados.get(str(steam_app_id), {})
        if not info.get("success"):
            _cache_genero_steam[steam_app_id] = None
            return None
        generos = info.get("data", {}).get("genres", [])
        nomes = ", ".join(g["description"] for g in generos[:3])
        _cache_genero_steam[steam_app_id] = nomes or None
        return nomes or None
    except Exception:
        _cache_genero_steam[steam_app_id] = None
        return None


def filtrar_promocoes_cheapshark(jogos, cotacao_dolar):
    print(f"🔎 CheapShark retornou {len(jogos)} itens brutos para a loja Xbox.")
    promocoes = []
    for jogo in jogos:
        desconto = float(jogo.get('savings', 0))
        metacritic = int(jogo.get('metacriticScore') or 0)
        nota_usuarios = int(jogo.get('steamRatingPercent') or 0)
        preco_usd = float(jogo.get('salePrice', 0))

        if preco_usd <= 0:
            continue
        if desconto < DESCONTO_MINIMO:
            continue
        if not (metacritic >= 70 or nota_usuarios >= 80):
            continue

        genero = buscar_genero_steam(jogo.get('steamAppID'))

        promocoes.append({
            'titulo': jogo['title'],
            'desconto': f"{desconto:.0f}%",
            'preco_brl': preco_usd * cotacao_dolar,
            'eh_gratis': False,
            'metacritic': metacritic,
            'nota_users': nota_usuarios,
            'genero': genero,
            'imagem': jogo.get('thumb'),
            'link': f"https://www.cheapshark.com/redirect?dealID={jogo['dealID']}",
            'id': f"cs_{jogo['dealID']}",
            'fonte': "CheapShark"
        })
    print(f"   ↳ Passaram no filtro: {len(promocoes)}")
    return promocoes


# ===================== FONTE 2: GamerPower (jogos grátis / giveaways) =====================

def buscar_gratis_gamerpower():
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
                print(f"⚪ Nenhum jogo grátis no momento para {plataforma}.")
                continue
            for item in dados:
                resultados[item['id']] = item
        except Exception as e:
            print(f"⚠️ Erro ao buscar GamerPower ({plataforma}): {e}")
            alertas_saude.append(f"GamerPower ({plataforma}): erro ao buscar dados ({e})")
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
            'genero': None,
            'imagem': item.get('image'),
            'link': item.get('open_giveaway_url') or item.get('gamerpower_url'),
            'id': f"gp_{item['id']}",
            'fonte': "GamerPower"
        })
    return gratis


# ===================== FONTE 3: IsThereAnyDeal (Microsoft Store oficial) =====================

SHOP_ID_MICROSOFT_STORE = 48


def buscar_promocoes_itad(cotacao_dolar):
    """Busca promoções da Microsoft Store através da API oficial da IsThereAnyDeal."""
    if not ITAD_API_KEY:
        print("ℹ️ ITAD_API_KEY não configurada — pulando fonte Microsoft Store (opcional).")
        return []

    try:
        url = "https://api.isthereanydeal.com/deals/v2"
        params = {
            "key": ITAD_API_KEY,
            "country": "BR",       # tenta já vir em reais
            "shops": SHOP_ID_MICROSOFT_STORE,
            "limit": 100,
            "sort": "-cut",        # maior desconto primeiro
        }
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        dados = resp.json()
        itens = dados.get("list", [])

        if len(itens) == 0:
            alertas_saude.append(
                "IsThereAnyDeal (Microsoft Store): retornou 0 itens — pode estar sem promoções "
                "no momento ou a chave/config mudou."
            )
        print(f"🔎 IsThereAnyDeal retornou {len(itens)} itens da Microsoft Store.")

        promocoes = []
        for item in itens:
            deal = item.get("deal", {})
            cut = deal.get("cut", 0)
            if cut < DESCONTO_MINIMO:
                continue

            preco_info = deal.get("price", {})
            preco_valor = preco_info.get("amount", 0)
            moeda = preco_info.get("currency", "USD")
            preco_brl = preco_valor if moeda == "BRL" else preco_valor * cotacao_dolar

            assets = item.get("assets", {})
            imagem = assets.get("banner300") or assets.get("boxart")

            promocoes.append({
                'titulo': item.get('title', 'Jogo'),
                'desconto': f"{cut}%",
                'preco_brl': preco_brl,
                'eh_gratis': preco_valor <= 0,
                'metacritic': 0,
                'nota_users': 0,
                'genero': None,
                'imagem': imagem,
                'link': deal.get('url'),
                'id': f"itad_{item['id']}",
                'fonte': "Microsoft Store (IsThereAnyDeal)"
            })

        print(f"   ↳ Passaram no filtro: {len(promocoes)}")
        return promocoes

    except Exception as e:
        print(f"⚠️ Erro ao buscar IsThereAnyDeal: {e}")
        alertas_saude.append(f"IsThereAnyDeal (Microsoft Store): erro ao buscar dados ({e})")
        return []


# ===================== TELEGRAM =====================

def montar_legenda(j):
    if j['eh_gratis']:
        legenda = f"🎮 <b>{j['titulo']}</b>\n💚 <b>GRÁTIS</b>\n"
    else:
        legenda = f"🎮 <b>{j['titulo']}</b>\n💰 <b>{j['desconto']} OFF</b> — R$ {j['preco_brl']:.2f}\n"
    if j.get('genero'):
        legenda += f"🏷️ Gênero: {j['genero']}\n"
    if j['metacritic'] > 0:
        legenda += f"⭐ Metacritic: {j['metacritic']}"
    if j['nota_users'] > 0:
        legenda += f" | Users: {j['nota_users']}%"
    legenda += f"\n📡 Fonte: {j['fonte']}"
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


def enviar_resumo_semanal(todas):
    """Toda semana (dia configurável), manda um resumo com tudo que ainda está
    valendo, mesmo que já tenha sido enviado como novidade antes."""
    if not todas:
        enviar_texto_telegram("📅 <b>RESUMO SEMANAL:</b> nenhuma promoção ativa no momento que bata os critérios.")
        return

    gratis = [j for j in todas if j['eh_gratis']]
    promocoes = [j for j in todas if not j['eh_gratis']]

    enviar_texto_telegram(f"📅 <b>RESUMO SEMANAL: {len(todas)} promoções/jogos grátis ainda valendo</b>")

    if gratis:
        enviar_texto_telegram("🆓 <b>Ainda grátis</b>")
        for j in gratis[:15]:
            enviar_foto_telegram(j['imagem'], montar_legenda(j))
            time.sleep(1)

    if promocoes:
        enviar_texto_telegram(f"💥 <b>Ainda em promoção ({DESCONTO_MINIMO}%+ OFF)</b>")
        for j in promocoes[:15]:
            enviar_foto_telegram(j['imagem'], montar_legenda(j))
            time.sleep(1)


def enviar_alerta_saude():
    """Se alguma fonte apresentou comportamento estranho, avisa separadamente."""
    if not alertas_saude:
        return
    mensagem = "⚠️ <b>Aviso de manutenção do bot</b>\n\n"
    mensagem += "Uma ou mais fontes de dados tiveram um comportamento inesperado hoje:\n\n"
    for item in alertas_saude:
        mensagem += f"• {item}\n"
    if LINK_ACTIONS:
        mensagem += f"\n🔧 <a href='{LINK_ACTIONS}'>Abrir Actions para rodar de novo manualmente</a>"
    try:
        enviar_texto_telegram(mensagem)
    except Exception as e:
        print(f"⚠️ Não foi possível nem enviar o alerta de saúde: {e}")


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

    # Fonte 3: promoções oficiais da Microsoft Store (via IsThereAnyDeal, opcional)
    promocoes_itad = buscar_promocoes_itad(cotacao_dolar)

    todas = promocoes + gratis + promocoes_itad

    # Resumo semanal: independente de haver novidade, roda no dia configurado
    eh_dia_de_resumo = datetime.now().weekday() == DIA_RESUMO_SEMANAL
    if eh_dia_de_resumo:
        enviar_resumo_semanal(todas)

    novas = [j for j in todas if j['id'] not in historico]

    if not novas:
        print("⚪ Nenhuma novidade hoje.")
        enviar_alerta_saude()
        return

    novas_gratis = [j for j in novas if j['eh_gratis']]
    novas_promocoes = [j for j in novas if not j['eh_gratis']]

    enviar_texto_telegram(f"🚨 <b>ALERTA: {len(novas)} JOIAS RARAS NO XBOX!</b>")

    if novas_gratis:
        enviar_texto_telegram("🆓 <b>JOGOS GRÁTIS</b>")
        for j in novas_gratis[:10]:
            enviar_foto_telegram(j['imagem'], montar_legenda(j))
            time.sleep(1)

    if novas_promocoes:
        enviar_texto_telegram(f"💥 <b>PROMOÇÕES ({DESCONTO_MINIMO}%+ OFF)</b>")
        for j in novas_promocoes[:10]:
            enviar_foto_telegram(j['imagem'], montar_legenda(j))
            time.sleep(1)

    print(f"✅ {len(novas)} novidades enviadas!")

    for j in novas:
        historico.add(j['id'])
    salvar_historico(historico)

    enviar_alerta_saude()


if __name__ == "__main__":
    main()
