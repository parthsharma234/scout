import time
import requests
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Tuple
from datetime import datetime, timezone
import logging
from bs4 import BeautifulSoup
import os
from dotenv import load_dotenv

load_dotenv()

from db import upsert_startup, refresh_top50, insert_raw_signal, mark_raw_processed
from nemotron import evaluate_post

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HN_BASE = "https://hacker-news.firebaseio.com/v0"

def scrape_hackernews() -> List[Dict[str, Any]]:
    logger.info("Scraping Hacker News (Deep Data Run)...")
    results = []
    
    endpoints = ["showstories", "topstories", "newstories"]
    all_ids = set()
    
    for ep in endpoints:
        try:
            r = requests.get(f"{HN_BASE}/{ep}.json", timeout=10)
            if r.status_code == 200:
                all_ids.update(r.json())
        except Exception as e:
            logger.error(f"Error fetching {ep}: {e}")
            
    story_ids = list(all_ids)
    logger.info(f"Found {len(story_ids)} total unique HN stories for inspection.")
    
    for i, sid in enumerate(story_ids):
        if i % 100 == 0:
            logger.info(f"HN Progress: {i}/{len(story_ids)}")
        try:
            item_r = requests.get(f"{HN_BASE}/item/{sid}.json", timeout=5)
            if item_r.status_code != 200:
                continue
            item = item_r.json()
            if not item or item.get('deleted') or item.get('dead'):
                continue
                
            title = item.get('title', '')
            url = item.get('url', f"https://news.ycombinator.com/item?id={sid}")
            text = item.get('text', '')
            score = int(item.get('score', 0))
            comments = int(item.get('descendants', 0))
            created_ts = int(item.get('time', 0))
            
            hours_since = (time.time() - created_ts) / 3600.0
            velocity = (score + comments) / max(1.0, hours_since)
            
            post_content = f"Title: {title}\nURL: {url}\nDescription: {text}"
            
            post = {
                "source": "hackernews",
                "source_url": url,
                "post_content": post_content,
                "engagement": {
                    "upvotes": score,
                    "comments": comments,
                    "hours_since_posted": round(hours_since, 1),
                    "velocity": round(velocity, 2)
                }
            }
            results.append(post)
            insert_raw_signal(post)
            time.sleep(0.01) # fast sleep to get through massive list
        except Exception as e:
            logger.warning(f"Error parsing sid {sid}: {e}")
            
    return results

def scrape_reddit() -> List[Dict[str, Any]]:
    logger.info("Scraping Reddit (Deep Data Run)...")
    subreddits = ["SideProject", "entrepreneur", "startups"]
    headers = {"User-Agent": "Mozilla/5.0 (Scout Data Engine v1.0)"}
    results = []
    
    for sub in subreddits:
        logger.info(f"Paginating r/{sub}...")
        after = None
        pages = 0
        while pages < 10:
            try:
                url = f"https://www.reddit.com/r/{sub}/new.json?limit=100"
                if after:
                    url += f"&after={after}"
                    
                r = requests.get(url, headers=headers, timeout=10)
                r.raise_for_status()
                data = r.json()
                posts = data.get('data', {}).get('children', [])
                
                if not posts:
                    break
                    
                for p in posts:
                    post = p['data']
                    title = post.get('title', '')
                    text = post.get('selftext', '')
                    url_val = f"https://reddit.com{post.get('permalink')}"
                    score = int(post.get('score', 0))
                    comments = int(post.get('num_comments', 0))
                    created_ts = int(post.get('created_utc', 0))
                    
                    hours_since = (time.time() - created_ts) / 3600.0
                    velocity = (score + comments) / max(1.0, hours_since)
                    
                    if "megathread" in title.lower() or "share your" in title.lower() or "how do you" in title.lower():
                        continue
                        
                    post_content = f"Title: {title}\nURL: {url_val}\nContent: {text}"
                    
                    p_dict = {
                        "source": "reddit",
                        "source_url": url_val,
                        "post_content": post_content,
                        "engagement": {
                            "upvotes": score,
                            "comments": comments,
                            "hours_since_posted": round(hours_since, 1),
                            "velocity": round(velocity, 2)
                        }
                    }
                    results.append(p_dict)
                    insert_raw_signal(p_dict)
                
                after = data.get('data', {}).get('after')
                if not after:
                    break
                pages += 1
                time.sleep(1) # rate limit
            except Exception as e:
                logger.error(f"Error scraping Reddit (r/{sub}): {e}")
                break
                
    return results

def scrape_rss(feed_url: str, source_name: str) -> List[Dict[str, Any]]:
    logger.info(f"Scraping {source_name} RSS...")
    results = []
    try:
        r = requests.get(feed_url, timeout=10)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        
        is_atom = 'w3.org/2005/Atom' in root.tag
        items = root.findall('.//item') if not is_atom else root.findall('.//{http://www.w3.org/2005/Atom}entry')
        
        for item in items[:200]:
            if is_atom:
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                title_node = item.find('atom:title', ns)
                title = title_node.text if title_node is not None else "No Title"
                
                link_node = item.find('atom:link', ns)
                link = link_node.attrib['href'] if link_node is not None else ""
                
                content_node = item.find('atom:content', ns)
                if content_node is None:
                    content_node = item.find('atom:summary', ns)
                content = content_node.text if content_node is not None else ""
                
                published_str = item.find('atom:published', ns)
                if published_str is None:
                    published_str = item.find('atom:updated', ns)
                published_str = published_str.text if published_str is not None else ""
                
                try:
                    dt_format = published_str.replace("Z", "+00:00") if "T" in published_str else published_str
                    published_dt = datetime.fromisoformat(dt_format)
                except:
                    published_dt = datetime.now(timezone.utc)
            else:
                title = getattr(item.find('title'), 'text', 'No Title')
                link = getattr(item.find('link'), 'text', '')
                content = getattr(item.find('description'), 'text', '')
                pubDate = getattr(item.find('pubDate'), 'text', '')
                
                try:
                    published_dt = datetime.strptime(pubDate, "%a, %d %b %Y %H:%M:%S %z")
                except:
                    published_dt = datetime.now(timezone.utc)
                    
            hours_since = (datetime.now(timezone.utc) - published_dt).total_seconds() / 3600.0
            
            p_dict = {
                "source": source_name,
                "source_url": link,
                "post_content": f"Title: {title}\nURL: {link}\nPitch: {content}",
                "engagement": {
                    "upvotes": 0,
                    "hours_since_posted": round(hours_since, 1)
                }
            }
            results.append(p_dict)
            insert_raw_signal(p_dict)
    except Exception as e:
        logger.error(f"Error scraping {source_name}: {e}")
    return results

def scrape_producthunt() -> List[Dict[str, Any]]:
    # ProductHunt uses a GraphQL endpoint for its API
    logger.info("Scraping ProductHunt via GraphQL API...")
    token = os.environ.get("PRODUCT_HUNT")
    results = []
    
    if not token:
        logger.warning("No PRODUCT_HUNT API token found in .env, skipping ProductHunt.")
        return results
        
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    url = "https://api.producthunt.com/v2/api/graphql"
    cursor = None
    
    # Scrape a few pages of data (e.g. 4 pages of 50 = 200 posts)
    for page in range(4):
        logger.info(f"Paginating ProductHunt page {page+1}...")
        
        cursor_arg = f', after: "{cursor}"' if cursor else ""
        query = f"""
        query {{
          posts(first: 50{cursor_arg}) {{
            edges {{
              node {{
                id
                name
                tagline
                url
                votesCount
                commentsCount
                createdAt
                description
              }}
            }}
            pageInfo {{
              hasNextPage
              endCursor
            }}
          }}
        }}
        """
        
        try:
            r = requests.post(url, headers=headers, json={"query": query}, timeout=15)
            r.raise_for_status()
            data = r.json()
            
            posts_data = data.get("data", {}).get("posts", {})
            edges = posts_data.get("edges", [])
            
            if not edges:
                break
                
            for edge in edges:
                node = edge.get("node", {})
                
                title = node.get("name", "")
                tagline = node.get("tagline", "")
                link = node.get("url", "")
                description = node.get("description", "")
                upvotes = node.get("votesCount", 0)
                comments = node.get("commentsCount", 0)
                created_str = node.get("createdAt", "")
                
                try:
                    dt_format = created_str.replace("Z", "+00:00") if "T" in created_str else created_str
                    published_dt = datetime.fromisoformat(dt_format)
                except:
                    published_dt = datetime.now(timezone.utc)
                    
                hours_since = (datetime.now(timezone.utc) - published_dt).total_seconds() / 3600.0
                velocity = (upvotes + comments) / max(1.0, hours_since)
                
                post_content = f"Title: {title}\nTagline: {tagline}\nURL: {link}\nDescription: {description}"
                
                p_dict = {
                    "source": "producthunt",
                    "source_url": link,
                    "post_content": post_content,
                    "engagement": {
                        "upvotes": upvotes,
                        "comments": comments,
                        "hours_since_posted": round(hours_since, 1),
                        "velocity": round(velocity, 2)
                    }
                }
                results.append(p_dict)
                insert_raw_signal(p_dict)
                
            page_info = posts_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
                
            cursor = page_info.get("endCursor")
            time.sleep(1) # ProductHunts rate limits
        except Exception as e:
            logger.error(f"Error scraping ProductHunt page {page+1}: {e}")
            break
            
    return results

def scrape_indiehackers() -> List[Dict[str, Any]]:
    logger.info("Scraping IndieHackers (Deep Data Run)...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    results = []
    seen_urls = set()
    
    # Scrape 10 pages for a deep run
    for page in range(1, 11):
        url = f"https://www.indiehackers.com/tech?page={page}"
        logger.info(f"Paginating IndieHackers page {page}...")
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            
            post_links = []
            for a in soup.find_all('a'):
                href = a.get('href', '')
                if '/post/' in href:
                    post_links.append(a)
                    
            if not post_links:
                 logger.warning(f"No posts found on page {page}, HTML layout might have changed")
                 break
                 
            for a_tag in post_links:
                link = a_tag.get('href', '')
                if link.startswith('/'):
                    link = f"https://www.indiehackers.com{link}"
                    
                if link in seen_urls:
                    continue
                seen_urls.add(link)
                
                title = a_tag.text.strip()
                if not title or len(title) < 5:
                    continue
                    
                upvotes = 0
                comments = 0
                
                hours_since = 12.0 
                velocity = (upvotes + comments) / max(1.0, hours_since)
                
                p_dict = {
                    "source": "indiehackers",
                    "source_url": link,
                    "post_content": f"Title: {title}\nURL: {link}",
                    "engagement": {
                        "upvotes": upvotes,
                        "comments": comments,
                        "hours_since_posted": round(hours_since, 1),
                        "velocity": round(velocity, 2)
                    }
                }
                results.append(p_dict)
                insert_raw_signal(p_dict)
                
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error scraping IndieHackers page {page}: {e}")
            break
            
    return results

def scrape_lobsters() -> List[Dict[str, Any]]:
    logger.info("Scraping Lobste.rs (Deep Data Run)...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    results = []
    
    # Scrape 5 pages for a structured data run
    for page in range(1, 6):
        # Lobste.rs newest pagination: /newest.json or /page/2.json 
        url = "https://lobste.rs/newest.json" if page == 1 else f"https://lobste.rs/page/{page}.json"
        
        logger.info(f"Paginating Lobste.rs page {page}...")
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                continue
                
            posts = r.json()
            if not posts or not isinstance(posts, list):
                break
                
            for item in posts:
                title = item.get("title", "")
                link = item.get("url", item.get("short_id_url", ""))
                description = item.get("description", "")
                upvotes = int(item.get("score", 0))
                comments = int(item.get("comment_count", 0))
                created_str = item.get("created_at", "")
                
                try:
                    dt_format = created_str.replace("Z", "+00:00") if "T" in created_str else created_str
                    published_dt = datetime.fromisoformat(dt_format)
                except:
                    published_dt = datetime.now(timezone.utc)
                    
                hours_since = (datetime.now(timezone.utc) - published_dt).total_seconds() / 3600.0
                velocity = (upvotes + comments) / max(1.0, hours_since)
                
                post_content = f"Title: {title}\nURL: {link}\nDescription: {description}"
                
                p_dict = {
                    "source": "lobsters",
                    "source_url": link,
                    "post_content": post_content,
                    "engagement": {
                        "upvotes": upvotes,
                        "comments": comments,
                        "hours_since_posted": round(hours_since, 1),
                        "velocity": round(velocity, 2)
                    }
                }
                results.append(p_dict)
                insert_raw_signal(p_dict)
                
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error scraping Lobste.rs page {page}: {e}")
            break
            
    return results

def run_pipeline() -> None:
    logger.info("Starting Scraper Pipeline...")
    all_posts = []
    
    all_posts.extend(scrape_hackernews())
    all_posts.extend(scrape_reddit())
    all_posts.extend(scrape_producthunt())
    all_posts.extend(scrape_indiehackers())
    all_posts.extend(scrape_lobsters())
    
    logger.info(f"Collected {len(all_posts)} massive batch of posts. Submitting unprocessed signals to Nemotron for evaluation...")
    
    # Process with Nemotron
    for i, post in enumerate(all_posts):
        if i % 10 == 0:
            logger.info(f"Evaluating Nemotron Target {i}/{len(all_posts)}...")
            
        result = evaluate_post(post['post_content'], post['source'], post['engagement'])
        mark_raw_processed(post['source_url'])
        
        if result and result.get('is_startup') is True:
            logger.info(f"✅ Startup Found: {result.get('startup_name')} (Score: {result.get('scout_score')})")
            
            startup_name = result.get('startup_name', 'Unknown')
            safe_id = "".join(c for c in startup_name.lower() if c.isalnum())
            if not safe_id:
                safe_id = f"startup_{int(time.time())}"
                
            startup_record = {
                "id": safe_id,
                "startup_name": startup_name,
                "one_liner": result.get('one_liner'),
                "vertical": result.get('vertical'),
                "business_model": result.get('business_model'),
                "geography": result.get('geography'),
                "stage": result.get('stage'),
                "team_signals": result.get('team_signals'),
                "traction_signals": result.get('traction_signals'),
                "scout_score": result.get('scout_score', 0),
                "source": post['source'],
                "source_url": post['source_url'],
                "raw_text": post['post_content']
            }
            upsert_startup(startup_record)
        else:
            time.sleep(0.3) 
            
    logger.info("Refreshing Top 50 Rankings...")
    refresh_top50()
    logger.info("Pipeline run complete.")

if __name__ == "__main__":
    run_pipeline()
