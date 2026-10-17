# scraper_traducteur.py
# Version robuste avec sélecteurs CSS et logs de débogage

import requests
from lxml import html
import logging
import time
import random
from datetime import datetime, timezone
from typing import List, Optional
import json
import os
from feedgen.feed import FeedGenerator
from deep_translator import GoogleTranslator

# -------------------- CONFIGURATION --------------------
SOURCE_URL = "https://pagesix.com/"
MAX_ARTICLES = 20
MIN_DELAY = 0.5
MAX_DELAY = 1.5
CACHE_FILE = "articles_cache.json"
FEED_FILE = "feed.xml"
DEBUG_HTML_FILE = "debug_page.html"   # Sauvegarde du HTML pour analyse

# -------------------- LOGGING --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# -------------------- TRADUCTION --------------------
def translate_text(text: str, target_lang: str = 'fr') -> str:
    if not text:
        return ""
    try:
        translator = GoogleTranslator(source='auto', target=target_lang)
        if len(text) > 5000:
            text = text[:5000]
        return translator.translate(text)
    except Exception as e:
        logger.warning(f"Erreur de traduction: {e}")
        return text

# -------------------- CHARGEMENT DE LA PAGE --------------------
def fetch_page(url: str) -> Optional[html.HtmlElement]:
    try:
        # Tentative avec cloudscraper (anti-bot)
        try:
            import cloudscraper
            scraper = cloudscraper.create_scraper()
            response = scraper.get(url, timeout=15)
        except ImportError:
            response = requests.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            })
        response.raise_for_status()
        
        # Sauvegarde du HTML pour débogage (si besoin)
        with open(DEBUG_HTML_FILE, 'w', encoding='utf-8') as f:
            f.write(response.text)
        logger.info(f"HTML sauvegardé dans {DEBUG_HTML_FILE}")

        return html.fromstring(response.content)
    except Exception as e:
        logger.error(f"Erreur lors du fetch de {url}: {e}")
        return None

# -------------------- CACHE --------------------
def load_cache() -> List[str]:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('urls', [])
        except Exception as e:
            logger.warning(f"Erreur de chargement du cache: {e}")
    return []

def save_cache(urls: List[str]):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'urls': urls, 'updated': datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Erreur de sauvegarde du cache: {e}")

# -------------------- EXTRACTION AVEC NOUVEAUX SELECTEURS --------------------
def scrape_naijanews_com() -> List[dict]:
    articles = []
    cache_urls = load_cache()
    new_urls = []

    tree = fetch_page(SOURCE_URL)
    if tree is None:
        return articles

    # NOUVEAU SELECTEUR UNIQUE pour les conteneurs d'articles
    # //div[contains(@class, 'story') and .//*[contains(@class, 'story__headline')] and .//*[contains(@class, 'story__image')]]
    article_nodes = tree.xpath("//div[contains(@class, 'story') and .//*[contains(@class, 'story__headline')] and .//*[contains(@class, 'story__image')]]")
    
    if not article_nodes:
        # Fallback: essayer un sélecteur plus large
        logger.warning("Aucun article trouvé avec le sélecteur principal, recherche de div.story...")
        article_nodes = tree.xpath("//div[contains(@class, 'story')]")
    
    logger.info(f"📌 Nombre d'articles détectés : {len(article_nodes)}")

    for node in article_nodes[:MAX_ARTICLES]:
        try:
            # --- TITRE (avec normalize-space) ---
            # normalize-space(.//*[contains(@class, 'story__headline')]/a)
            title_elem = node.xpath(".//*[contains(@class, 'story__headline')]/a")
            title = ""
            if title_elem:
                title = title_elem[0].text_content().strip()
            if not title:
                continue

            # --- URL ---
            # .//*[contains(@class, 'story__headline')]/a/@href
            url_elem = node.xpath(".//*[contains(@class, 'story__headline')]/a/@href")
            url = url_elem[0].strip() if url_elem else ""
            if not url:
                continue
            if url.startswith('/'):
                url = 'https://pagesix.com' + url
            elif not url.startswith('http'):
                continue

            # Cache / doublon
            if url in cache_urls:
                logger.debug(f"⏭️ Article déjà scrapé: {url}")
                continue
            if any(a['url'] == url for a in articles):
                continue

            # --- IMAGE ---
            # .//div[contains(@class, 'story__image')]//img/@src
            img_elem = node.xpath(".//div[contains(@class, 'story__image')]//img/@src")
            image = img_elem[0].strip() if img_elem else ""
            if image and image.startswith('/'):
                image = 'https://pagesix.com' + image

            # --- DESCRIPTION (avec normalize-space) ---
            # normalize-space(.//*[contains(@class, 'story__excerpt')])
            desc_elem = node.xpath(".//*[contains(@class, 'story__excerpt')]")
            desc = desc_elem[0].text_content().strip() if desc_elem else ""
            
            if not desc:
                # Fallback: utiliser l'alt de l'image ou le titre
                alt_elem = node.xpath(".//div[contains(@class, 'story__image')]//img/@alt")
                if alt_elem:
                    desc = alt_elem[0].strip()
            if not desc:
                desc = title

            # --- TEMPS DE PUBLICATION ---
            # ( .//time/@datetime | .//*[contains(@class, 'meta')]/text() )
            date_elem = node.xpath(".//time/@datetime | .//*[contains(@class, 'meta')]/text()")
            date_str = date_elem[0].strip() if date_elem else ""
            
            date_obj = datetime.now(timezone.utc)
            if date_str:
                try:
                    # Essayer de parser la date
                    date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                except:
                    try:
                        # Autres formats possibles
                        from dateutil import parser
                        date_obj = parser.parse(date_str)
                    except:
                        logger.debug(f"Impossible de parser la date: {date_str}")
                        date_obj = datetime.now(timezone.utc)

            # --- TRADUCTION ---
            logger.info(f"🌐 Traduction de: {title[:30]}...")
            title_fr = translate_text(title)
            description_fr = translate_text(desc)

            article = {
                'title': title,
                'title_fr': title_fr,
                'url': url,
                'image': image,
                'description': desc,
                'description_fr': description_fr,
                'date': date_obj,
                'date_str': date_obj.isoformat()
            }

            articles.append(article)
            new_urls.append(url)
            logger.info(f"✨ Article ajouté: {title_fr[:40]}...")
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        except Exception as e:
            logger.debug(f"Erreur sur un article : {e}")
            continue

    # Mise à jour du cache
    if new_urls:
        all_urls = cache_urls + new_urls
        if len(all_urls) > 500:
            all_urls = all_urls[-500:]
        save_cache(all_urls)

    return articles

# -------------------- GÉNÉRATION DU FEED --------------------
def generate_feed(articles: List[dict], output_file: str = FEED_FILE):
    fg = FeedGenerator()
    fg.title("Pagesix - Actualités traduites en français")
    fg.description("Flux RSS des actualités de Pagesix automatiquement traduites en français" if articles else "Aucun article disponible actuellement")
    fg.link(href="https://buzzplus225.github.io/sixpage.github.io/", rel="alternate")
    fg.link(href="https://buzzplus225.github.io/sixpage.github.io/feed.xml", rel="self")
    fg.language("fr")
    fg.lastBuildDate(datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"))
    fg.generator("Scraper Traducteur Pagesix v2.0")

    if not articles:
        rss_str = fg.rss_str(pretty=True)
        with open(output_file, 'wb') as f:
            f.write(rss_str)
        logger.info(f"✅ Feed RSS vide créé: {output_file}")
        return

    for article in articles[:20]:
        fe = fg.add_entry()
        fe.title(article.get('title_fr', article.get('title', '')))
        fe.link(href=article['url'])
        fe.guid(article['url'], permalink=True)
        fe.description(article.get('description_fr', article.get('description', '')))
        
        content = f"<p>{article.get('description_fr', article.get('description', ''))}</p>"
        if article.get('image'):
            content = f'<img src="{article["image"]}" alt="{article.get("title", "")}" style="max-width:100%;"/><br/>{content}'
        fe.content(content, type="CDATA")
        
        if article.get('date'):
            fe.pubDate(article['date'].strftime("%a, %d %b %Y %H:%M:%S +0000"))
        else:
            fe.pubDate(datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"))

    rss_str = fg.rss_str(pretty=True)
    with open(output_file, 'wb') as f:
        f.write(rss_str)
    
    logger.info(f"✅ Feed RSS généré: {output_file} ({len(articles)} articles)")

def save_json(articles: List[dict], output_file: str = "articles.json"):
    try:
        articles_serializable = []
        for a in articles:
            a_copy = a.copy()
            if 'date' in a_copy and a_copy['date']:
                a_copy['date'] = a_copy['date'].isoformat()
            articles_serializable.append(a_copy)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(articles_serializable, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ JSON sauvegardé: {output_file}")
    except Exception as e:
        logger.error(f"Erreur de sauvegarde JSON: {e}")

# -------------------- MAIN --------------------
if __name__ == "__main__":
    print("🚀 Début du scraping avec sélecteurs Pagesix...")
    start_time = time.time()
    
    articles = scrape_naijanews_com()
    
    elapsed = time.time() - start_time
    print(f"✅ Scraping terminé. {len(articles)} articles récupérés en {elapsed:.2f}s")
    
    if articles:
        generate_feed(articles)
        save_json(articles)
        print(f"✅ Fichiers générés: {FEED_FILE}, articles.json")
    else:
        print("❌ Aucun article trouvé")
        generate_feed([], "feed.xml")
        print("⚠️ Feed vide créé")