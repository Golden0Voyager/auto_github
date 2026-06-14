import os
import re
import time
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from src.config import AppConfig

class GitHubCrawler:
    """Crawls GitHub Trending and fetches recent activity of target organizations/users."""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }
        
        # Use GITHUB_TOKEN if available in env to avoid rate limits
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.api_headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "auto-github-curator"
        }
        if self.github_token:
            self.api_headers["Authorization"] = f"token {self.github_token}"

    def crawl_trending(self, since: str = "daily") -> List[Dict[str, Any]]:
        """Scrapes the GitHub Trending page.
        
        Args:
            since: 'daily', 'weekly', or 'monthly'
        """
        since_param = "daily"
        if since == "weekly":
            since_param = "weekly"
        elif since == "monthly":
            since_param = "monthly"
            
        url = f"https://github.com/trending?since={since_param}"
        print(f"[Crawl] Fetching GitHub Trending ({since_param}) from {url}...")
        
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            if response.status_code != 200:
                print(f"[Crawl Warning] Failed to scrape trending, status code: {response.status_code}")
                return []
                
            soup = BeautifulSoup(response.text, "html.parser")
            articles = soup.find_all("article", class_="Box-row")
            
            trending_repos = []
            max_repos = self.config.github.max_trending_repos
            
            for article in articles[:max_repos]:
                # 1. Parse repository title (owner and repo name)
                title_tag = article.find("h2", class_=lambda x: x and "lh-condensed" in x) or article.find("h1")
                if not title_tag:
                    title_tag = article.find("h2")
                
                if not title_tag or not title_tag.find("a"):
                    continue
                    
                a_tag = title_tag.find("a")
                href = a_tag["href"].strip()
                # href is usually "/owner/repo"
                parts = [p for p in href.split("/") if p]
                if len(parts) < 2:
                    continue
                owner, repo_name = parts[0], parts[1]
                repo_url = f"https://github.com/{owner}/{repo_name}"
                
                # 2. Parse description
                desc_tag = article.find("p", class_=lambda x: x and "color-fg-muted" in x)
                desc = desc_tag.text.strip() if desc_tag else ""
                
                # 3. Parse language
                lang_tag = article.find(itemprop="programmingLanguage")
                lang = lang_tag.text.strip() if lang_tag else "Unknown"
                
                # 4. Parse stars & forks
                # Stars and forks are usually in links inside a specific div
                meta_div = article.find("div", class_="f6")
                stars = 0
                forks = 0
                added_stars = ""
                
                if meta_div:
                    links = meta_div.find_all("a", class_="Link--muted")
                    for link in links:
                        link_href = link.get("href", "")
                        if "stargazers" in link_href:
                            stars_text = link.text.strip().replace(",", "")
                            try:
                                stars = int(stars_text)
                            except ValueError:
                                pass
                        elif "forks" in link_href or "network/members" in link_href:
                            forks_text = link.text.strip().replace(",", "")
                            try:
                                forks = int(forks_text)
                            except ValueError:
                                pass
                                
                    # Period stars (e.g. "123 stars today" / "123 stars this week")
                    # Find all span elements with d-inline-block and check which one contains "stars"
                    spans = meta_div.find_all("span", class_="d-inline-block")
                    for span in spans:
                        if "stars" in span.text.lower():
                            added_stars = span.text.strip()
                            break
                    
                    # If not found via spans, fallback to float-sm-right span or last child
                    if not added_stars:
                        added_stars_float = meta_div.find("span", class_=lambda x: x and "float" in x)
                        if added_stars_float:
                            added_stars = added_stars_float.text.strip()
                        else:
                            added_stars = meta_div.text.strip().split("\n")[-1].strip()
                
                trending_repos.append({
                    "owner": owner,
                    "name": repo_name,
                    "full_name": f"{owner}/{repo_name}",
                    "url": repo_url,
                    "description": desc,
                    "language": lang,
                    "stars": stars,
                    "forks": forks,
                    "period_stars": added_stars,
                    "source": "trending",
                    "timeframe": since
                })
                
            print(f"[Crawl] Successfully scraped {len(trending_repos)} trending repositories.")
            return trending_repos
            
        except Exception as e:
            print(f"[Crawl Error] Exception during scraping trending: {e}")
            return []

    def scrape_readme(self, full_name: str) -> str:
        """Fetch README.md content from a GitHub repository.

        Tries main branch first, falls back to master.
        Returns empty string on failure.
        """
        for branch in ("main", "master"):
            url = f"https://raw.githubusercontent.com/{full_name}/{branch}/README.md"
            try:
                resp = requests.get(url, headers=self.headers, timeout=8)
                if resp.status_code == 200 and len(resp.text) > 50:
                    return resp.text[:4000]
            except requests.RequestException:
                continue
        return ""

    def fetch_giant_repos(self) -> List[Dict[str, Any]]:
        """Fetches active repos of LLM giants and key individuals via GitHub API."""
        orgs = self.config.github.monitored_orgs
        users = self.config.github.monitored_users
        max_repos = self.config.github.max_org_repos
        
        all_giant_repos = []
        
        # 1. Fetch Organizations
        for org in orgs:
            repos = self._get_user_or_org_repos(org, is_org=True, limit=max_repos)
            all_giant_repos.extend(repos)
            time.sleep(1) # Prevent aggressive requests
            
        # 2. Fetch Individual Users
        for user in users:
            repos = self._get_user_or_org_repos(user, is_org=False, limit=max_repos)
            all_giant_repos.extend(repos)
            time.sleep(1)
            
        print(f"[Crawl] Successfully fetched {len(all_giant_repos)} total repos from LLM giants & key users.")
        return all_giant_repos

    def _get_user_or_org_repos(self, name: str, is_org: bool = True, limit: int = 5) -> List[Dict[str, Any]]:
        """Helper to fetch repositories for a user/org from GitHub API."""
        entity_type = "orgs" if is_org else "users"
        url = f"https://api.github.com/{entity_type}/{name}/repos?sort=updated&direction=desc&per_page=20"
        
        print(f"[Crawl] Fetching active repos for {entity_type[:-1]} '{name}' from API...")
        
        try:
            response = requests.get(url, headers=self.api_headers, timeout=15)
            if response.status_code == 403 and "rate limit" in response.text.lower():
                print(f"[Crawl API Warning] Rate limit hit for {name}. Skipping...")
                return []
                
            if response.status_code != 200:
                print(f"[Crawl API Warning] Failed to fetch {name} repos, status: {response.status_code}")
                return []
                
            repos_data = response.json()
            repos = []
            
            # Sort by pushed_at to find truly active repos, exclude forks unless heavily starred
            active_repos = []
            for r in repos_data:
                if r.get("fork") and r.get("stargazers_count", 0) < 100:
                    continue # Ignore small forks to reduce noise
                active_repos.append(r)
                
            # Sort active repos by updated_at or pushed_at (descending)
            active_repos.sort(key=lambda x: x.get("pushed_at", x.get("updated_at", "")), reverse=True)
            
            for repo in active_repos[:limit]:
                repos.append({
                    "owner": name,
                    "name": repo.get("name"),
                    "full_name": repo.get("full_name"),
                    "url": repo.get("html_url"),
                    "description": repo.get("description") or "",
                    "language": repo.get("language") or "Unknown",
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "pushed_at": repo.get("pushed_at", ""),
                    "updated_at": repo.get("updated_at", ""),
                    "source": "llm_giant",
                    "timeframe": "recent_activity"
                })
            return repos
        except Exception as e:
            print(f"[Crawl API Error] Exception fetching {name} repos: {e}")
            return []
            
    def get_mock_data(self) -> List[Dict[str, Any]]:
        """Generates realistic mock data for local testing and offline verification."""
        print("[Crawl] Generating high-fidelity mock data for verification...")
        return [
            {
                "owner": "deepseek-ai",
                "name": "DeepSeek-V3",
                "full_name": "deepseek-ai/DeepSeek-V3",
                "url": "https://github.com/deepseek-ai/DeepSeek-V3",
                "description": "DeepSeek-V3 is a strong Mixture-of-Experts (MoE) language model with 671B total parameters and 37B active parameters, utilizing Multi-head Latent Attention (MLA) for efficient KV Cache compression.",
                "language": "Python",
                "stars": 15200,
                "forks": 1200,
                "period_stars": "850 stars today",
                "source": "trending",
                "timeframe": "daily"
            },
            {
                "owner": "deepseek-ai",
                "name": "DeepSeek-R1",
                "full_name": "deepseek-ai/DeepSeek-R1",
                "url": "https://github.com/deepseek-ai/DeepSeek-R1",
                "description": "DeepSeek-R1: Incentive reasoning capability with large-scale Reinforcement Learning. Features native reasoning trace output.",
                "language": "Python",
                "stars": 48200,
                "forks": 5100,
                "period_stars": "2300 stars today",
                "source": "trending",
                "timeframe": "daily"
            },
            {
                "owner": "lucidrains",
                "name": "MLA-pytorch",
                "full_name": "lucidrains/MLA-pytorch",
                "url": "https://github.com/lucidrains/MLA-pytorch",
                "description": "PyTorch implementation of Multi-Head Latent Attention (MLA) as introduced in DeepSeek-V2/V3. Optimized for low KV cache footprint.",
                "language": "Python",
                "stars": 850,
                "forks": 65,
                "period_stars": "120 stars today",
                "source": "trending",
                "timeframe": "daily"
            },
            {
                "owner": "openai",
                "name": "whisper",
                "full_name": "openai/whisper",
                "url": "https://github.com/openai/whisper",
                "description": "Robust Speech Recognition via Large-Scale Weak Supervision. Highly accurate audio transcription framework.",
                "language": "Python",
                "stars": 62000,
                "forks": 8100,
                "period_stars": "1,200 stars this week",
                "source": "trending",
                "timeframe": "weekly"
            },
            {
                "owner": "mistralai",
                "name": "mistral-inference",
                "full_name": "mistralai/mistral-inference",
                "url": "https://github.com/mistralai/mistral-inference",
                "description": "Official inference library for Mistral AI open weight models, optimized for speed and footprint.",
                "language": "Python",
                "stars": 4500,
                "forks": 390,
                "period_stars": "350 stars this week",
                "source": "trending",
                "timeframe": "weekly"
            },
            {
                "owner": "anthropics",
                "name": "claude-code",
                "full_name": "anthropics/claude-code",
                "url": "https://github.com/anthropics/claude-code",
                "description": "Terminal-driven coding assistant by Anthropic. Highly optimized for contextual codebase reasoning.",
                "language": "TypeScript",
                "stars": 18200,
                "forks": 1400,
                "period_stars": "6,500 stars this month",
                "source": "trending",
                "timeframe": "monthly"
            },
            {
                "owner": "microsoft",
                "name": "AutoDev",
                "full_name": "microsoft/AutoDev",
                "url": "https://github.com/microsoft/AutoDev",
                "description": "Autonomous AI Agent framework for software development, orchestrating code edits, build tests, and git commits.",
                "language": "Kotlin",
                "stars": 8200,
                "forks": 910,
                "period_stars": "2,100 stars this month",
                "source": "trending",
                "timeframe": "monthly"
            },
            {
                "owner": "google-research",
                "name": "sima",
                "full_name": "google-research/sima",
                "url": "https://github.com/google-research/sima",
                "description": "A Scalable Instructable Multiworld Agent (SIMA) for 3D virtual environment interaction.",
                "language": "Python",
                "stars": 1200,
                "forks": 140,
                "pushed_at": "2026-05-22T10:00:00Z",
                "source": "llm_giant",
                "timeframe": "recent_activity"
            },
            {
                "owner": "meta-llama",
                "name": "llama3",
                "full_name": "meta-llama/llama3",
                "url": "https://github.com/meta-llama/llama3",
                "description": "Inference code and model definitions for Llama 3 models.",
                "language": "Python",
                "stars": 34000,
                "forks": 4200,
                "pushed_at": "2026-05-23T01:00:00Z",
                "source": "llm_giant",
                "timeframe": "recent_activity"
            }
        ]
