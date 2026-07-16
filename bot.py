import requests
import json
import os
import time
from datetime import datetime

# ===== CONFIGURAÇÕES (vem do GitHub Secrets) =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== CONFIGURAÇÕES DO FILTRO =====
DESCONTO_MINIMO = 95  # % de desconto mínimo para considerar "joia rara"

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


def buscar_promocoes():
    url = "https://www.cheapshark.com/api/1.0/deals"
    params = {
        'storeID': 11,        # Xbox/Microsoft Store
        'sortBy': 'Savings',
        'pageSize': 60,
        'upperPrice': 15      # só jogos baratos (joias escondidas!)
    }

    response = requests.get(url, params=params)
    response.raise_for_status()
    return response.json()


def filtrar_joias(jogos, cotacao_dolar):
    joias = []
    for jogo in jogos:
        desconto = float(jogo.get('savings', 0))
        metacritic = int(jogo.get('metacriticScore') or 0)
        nota_usuarios = int(jogo.get('steamRatingPercent') or 0)
        preco_usd = float(jogo.get('salePrice', 0))

        # REGRA DE OURO: desconto mínimo E (nota boa OU é grátis)
        eh_gratis = preco_usd <= 0.0
        if desconto >= DESCONTO_MINIMO and (metacritic >= 70 or nota_usuarios >= 80 or eh_gratis):
            joias.append({
                'titulo': jogo['title'],
                'desconto': f"{desconto:.0f}%",
                'preco_usd': preco_usd,
                'preco_brl': preco_usd * cotacao_dolar,
                'eh_gratis': eh_gratis,
                'metacritic': metacritic,
                'nota_users': nota_usuarios,
                'imagem': jogo.get('thumb'),  # miniatura fornecida pela CheapShark
                'link': f"https://www.cheapshark.com/redirect?dealID={jogo['dealID']}",
                'id': jogo['dealID']
            })
    return joias


def montar_legenda(j):
    if j['eh_gratis']:
        legenda = f"🎮 <b>{j['titulo']}</b>\n💚 <b>GRÁTIS</b>\n"
    else:
        legenda = f"🎮 <b>{j['titulo']}</b>\n💰 <b>{j['desconto']} OFF</b> — R$ {j['preco_brl']:.2f}\n"
    if j['metacritic'] > 0:
        legenda += f"⭐ Metacritic: {j['metacritic']}"
    if j['nota_users'] > 0:
        legenda += f" | Users: {j['nota_users']}%"
    legenda += f"\n🔗 <a href='{j['link']}'>Ver na loja</a>"
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

    jogos = buscar_promocoes()
    joias = filtrar_joias(jogos, cotacao_dolar)

    # Filtra só as NOVAS (que ainda não foram enviadas)
    novas_joias = [j for j in joias if j['id'] not in historico]

    if not novas_joias:
        print("⚪ Nenhuma joia nova hoje.")
        return

    # Separa jogos grátis das promoções normais
    gratis = [j for j in novas_joias if j['eh_gratis']]
    promocoes = [j for j in novas_joias if not j['eh_gratis']]

    # Mensagem de abertura (resumo)
    enviar_texto_telegram(f"🚨 <b>ALERTA: {len(novas_joias)} JOIAS RARAS NO XBOX!</b>")

    if gratis:
        enviar_texto_telegram("🆓 <b>JOGOS GRÁTIS</b>")
        for j in gratis[:10]:
            enviar_foto_telegram(j['imagem'], montar_legenda(j))
            time.sleep(1)  # evita atingir limite de envio do Telegram

    if promocoes:
        enviar_texto_telegram(f"💥 <b>PROMOÇÕES ({DESCONTO_MINIMO}%+ OFF)</b>")
        for j in promocoes[:10]:
            enviar_foto_telegram(j['imagem'], montar_legenda(j))
            time.sleep(1)

    print(f"✅ {len(novas_joias)} promoções enviadas!")

    # Atualiza histórico
    for j in novas_joias:
        historico.add(j['id'])
    salvar_historico(historico)


if __name__ == "__main__":
    main()
