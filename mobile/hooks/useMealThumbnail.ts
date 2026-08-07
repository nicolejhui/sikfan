import { useEffect, useState } from 'react';
import { getMealImage } from '../api/meals';

// Rendering-layer cache, not app state — doesn't need to survive reloads.
const cache = new Map<string, string>();

export function useMealThumbnail(meal: { meal_id: string; image_url: string | null }): string | null {
  const { meal_id, image_url } = meal;
  const isLocal = !!image_url && image_url.startsWith('file://');
  const [remoteUri, setRemoteUri] = useState<string | null>(
    !isLocal && image_url ? cache.get(meal_id) ?? null : null
  );

  useEffect(() => {
    if (isLocal || !image_url) return;
    const cached = cache.get(meal_id);
    if (cached) {
      setRemoteUri(cached);
      return;
    }
    let cancelled = false;
    getMealImage(meal_id)
      .then((uri) => {
        if (cancelled) return;
        cache.set(meal_id, uri);
        setRemoteUri(uri);
      })
      .catch(() => {
        // Fetch failed (404/network) — leave remoteUri null so the caller falls back to placeholder.
      });
    return () => {
      cancelled = true;
    };
  }, [meal_id, image_url, isLocal]);

  if (isLocal) return image_url;
  return remoteUri;
}
