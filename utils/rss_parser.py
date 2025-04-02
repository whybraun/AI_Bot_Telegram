import feedparser
from datetime import datetime
import re
import logging
import requests

logger = logging.getLogger(__name__)

def clean_html(raw_html):
    """Очистка текста от HTML-тегов"""
    return re.sub(r'<[^>]+>', '', str(raw_html or ''))

def get_source_meta(url):
    """Определение источника с защитой от ошибок"""
    try:
        if 'reddit.com' in url:
            parts = url.split('/')
            subreddit = parts[4] if len(parts) > 4 else 'reddit'
            return f"Reddit/{subreddit}", "👥"
        elif 'arxiv.org' in url:
            return "arXiv", "📜"
        elif 'technologyreview.com' in url:
            return "MIT Tech Review", "🔬"
        elif 'openai.com' in url:
            return "OpenAI", "🤖"
        elif 'deepmind.com' in url:
            return "DeepMind", "🧠"
        else:
            domain = url.split('/')[2]
            return domain.replace('www.', ''), "🌐"
    except Exception as e:
        logger.warning(f"Ошибка определения источника: {str(e)}")
        return "Unknown", "❓"

def parse_rss(urls):
    """Улучшенный парсинг RSS с балансировкой источников"""
    entries = []
    if not urls:
        logger.warning("Получен пустой список RSS-лент")
        return entries

    successful_sources = 0
    
    for url in urls:
        try:
            if not url.startswith('http'):
                logger.warning(f"Пропускаем неверный URL: {url}")
                continue
                
            logger.info(f"Загрузка новостей из: {url}")
            
            # Специальные заголовки для Reddit
            headers = {'User-Agent': 'Mozilla/5.0'} if 'reddit.com' in url else {}
            feed = feedparser.parse(url, request_headers=headers)
            
            if not feed.entries:
                logger.warning(f"Нет записей в {url}")
                continue
                
            source, emoji = get_source_meta(url)
            successful_sources += 1
            logger.info(f"Обработка {source} ({url}), найдено {len(feed.entries)} записей")
            
            # Берем по 2 новости с каждого источника для баланса
            for entry in feed.entries[:2]:
                try:
                    title = clean_html(entry.get('title', ''))[:200] or 'Без названия'
                    description = clean_html(entry.get('summary', entry.get('description', '')))[:500]
                    link = entry.get('link', '')
                    
                    # Пропускаем записи без ссылки или с ссылкой на сам RSS
                    if not link or link == url:
                        continue
                        
                    pub_date = entry.get('published', '')
                    entries.append({
                        'title': f"{emoji} {title}",
                        'description': description,
                        'source': source,
                        'url': link,
                        'date': pub_date if pub_date else datetime.now().isoformat()
                    })
                except Exception as e:
                    logger.error(f"Ошибка обработки записи из {source}: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка парсинга {url}: {str(e)}")
    
    # Сортируем новости по дате (свежие сначала)
    entries.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    logger.info(f"Успешно обработано {successful_sources}/{len(urls)} источников, всего новостей: {len(entries)}")
    return entries