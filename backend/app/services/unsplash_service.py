"""Unsplash 图片服务（可选；无 Key 时安全降级为空结果）"""

from typing import List, Optional

import httpx

from ..config import get_settings


class UnsplashService:
    """Unsplash 图片搜索"""

    def __init__(self):
        settings = get_settings()
        self.access_key = settings.unsplash_access_key

    def search_photos(self, query: str, per_page: int = 5) -> List[dict]:
        """搜索图片；未配置 Key 或请求失败时返回空列表"""
        if not self.access_key:
            return []
        try:
            response = httpx.get(
                "https://api.unsplash.com/search/photos",
                params={
                    "query": query,
                    "per_page": per_page,
                    "client_id": self.access_key,
                },
                timeout=10,
            )
            response.raise_for_status()
            photos = []
            for photo in response.json().get("results", []):
                photos.append(
                    {
                        "id": photo.get("id"),
                        "url": photo.get("urls", {}).get("regular"),
                        "thumb": photo.get("urls", {}).get("thumb"),
                        "description": photo.get("description")
                        or photo.get("alt_description"),
                        "photographer": photo.get("user", {}).get("name"),
                    }
                )
            return photos
        except Exception as exc:
            print(f"⚠️  Unsplash 搜索失败: {exc}")
            return []

    def get_photo_url(self, query: str) -> Optional[str]:
        """获取单张图片 URL"""
        photos = self.search_photos(query, per_page=1)
        return photos[0]["url"] if photos else None


_unsplash_service: Optional[UnsplashService] = None


def get_unsplash_service() -> UnsplashService:
    """获取 Unsplash 服务实例（单例）"""
    global _unsplash_service
    if _unsplash_service is None:
        _unsplash_service = UnsplashService()
    return _unsplash_service
