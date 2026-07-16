import requests
import json
import os
from datetime import datetime

# ===== CONFIGURAÇÕES (vem do GitHub Secrets) =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

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

def buscar_promocoes():
    url = "https://www.cheapshark.com/api/1.0/deals"
    params = {
        'storeID': 11,        # Xbox/Microsoft Store
        'sortBy': 'Savings',
        'pageSize': 60,
        'upperPrice': 15      # só jogos baratos (joias escondidas!)
    }
    
    response = requests.get(url, params=params)
    return response.json()

def filtrar_joias(jogos):
    joias = []
    for jogo in jogos:
        desconto = float(jogo.get('savings', 0))
        metacritic = int(jogo.get('metacriticScore', 0))
        nota_usuarios = int(jogo.get('steamRatingPercent', 0))
        
        # REGRA DE OURO: 90%+ OFF E nota boa
        if desconto >= 90 and (metacritic >= 70 or nota_usuarios >= 80):
            joias.append({
                'titulo': jogo['title'],
                'desconto': f"{desconto:.0f}%",
                'preco_usd': jogo['salePrice'],
                'metacritic': metacritic,
                'nota_users': nota_usuarios,
                'link': jogo['dealURL'],
                'id': jogo['dealID']
            })
    return joias

def enviar_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    dados = {
        "chat_id": CHAT_ID,
        "text": mensagem,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    requests.post(url, data=dados)

def main():
    print(f"🕐 Rodando em {datetime.now()}")
    
    historico = carregar_historico()
    jogos = buscar_promocoes()
    joias = filtrar_joias(jogos)
    
    # Filtra só as NOVAS (que ainda não foram enviadas)
    novas_joias = [j for j in joias if j['id'] not in historico]
    
    if not novas_joias:
        print("⚪ Nenhuma joia nova hoje.")
        return
    
    # Monta a mensagem
    mensagem = f"🚨 <b>ALERTA: {len(novas_joias)} JOIAS RARAS NO XBOX!</b>\n\n"
    
    for j in novas_joias[:10]:  # limita a 10 para não floodar
        mensagem += f"🎮 <b>{j['titulo']}</b>\n"
        mensagem += f"💰 <b>{j['desconto']} OFF</b> — USD {j['preco_usd']}\n"
        if j['metacritic'] > 0:
            mensagem += f"⭐ Metacritic: {j['metacritic']}"
        if j['nota_users'] > 0:
            mensagem += f" | Users: {j['nota_users']}%"
        mensagem += f"\n🔗 <a href='{j['link']}'>Ver na loja</a>\n\n"
    
    # Envia no Telegram
    enviar_telegram(mensagem)
    print(f"✅ {len(novas_joias)} promoções enviadas!")
    
    # Atualiza histórico
    for j in novas_joias:
        historico.add(j['id'])
    salvar_historico(historico)

if __name__ == "__main__":
    main()
