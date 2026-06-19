#!/usr/bin/env bash
set -e

echo "=== Syncing ChromaDB from live Fly.io container ==="

# Step 1: Create a WAL-consistent snapshot on the container using SQLite's online
# backup API. shutil.copytree is NOT used — it's a plain filesystem copy and can
# capture inconsistent WAL state during a live write.
fly ssh console -C "python3 -c \"
import sqlite3, shutil, os
os.makedirs('/tmp/chroma_snapshot', exist_ok=True)
# SQLite online backup — WAL-safe, consistent even during concurrent writes
src = sqlite3.connect('/app/data/embeddings/chroma.sqlite3')
dst = sqlite3.connect('/tmp/chroma_snapshot/chroma.sqlite3')
src.backup(dst)
src.close(); dst.close()
# Copy non-SQLite files (ChromaDB collection UUID folders with vector data)
for item in os.listdir('/app/data/embeddings'):
    if not item.endswith('.sqlite3'):
        s = '/app/data/embeddings/' + item
        d = '/tmp/chroma_snapshot/' + item
        if os.path.isdir(s): shutil.copytree(s, d)
        else: shutil.copy2(s, d)
print('Snapshot written to /tmp/chroma_snapshot')
\""

# Step 2: Pull the snapshot — fail fast before touching local data
rm -rf data/embeddings_fly_latest
fly ssh sftp get -r /tmp/chroma_snapshot data/embeddings_fly_latest || exit 1

# Step 3: Verify non-empty BEFORE touching local embeddings
COUNT=$(python3 -c "
import chromadb
client = chromadb.PersistentClient(path='data/embeddings_fly_latest')
col = client.get_or_create_collection('dishes')
print(col.count())
")

if [ "$COUNT" -le 0 ]; then
  echo "ERROR: pulled ChromaDB has 0 embeddings — aborting swap to protect local data."
  rm -rf data/embeddings_fly_latest
  exit 1
fi

echo "Live container ChromaDB: $COUNT embeddings — looks good."

# Step 4: Safe to swap — local data only removed after successful pull + verify
rm -rf data/embeddings
mv data/embeddings_fly_latest data/embeddings
echo "Local data/embeddings replaced with live container state."
echo "Run 'bash scripts/predeploy.sh && fly deploy' to ship it."
