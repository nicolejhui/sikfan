#!/usr/bin/env bash
set -e

SNAPSHOT_DIR="deploy_snapshot/embeddings"
SOURCE_DIR="data/embeddings"

if [ ! -d "$SOURCE_DIR" ]; then
  echo "ERROR: $SOURCE_DIR not found — cannot create snapshot."
  exit 1
fi

echo "=== Pre-deploy ChromaDB snapshot ==="
python3 -c "
import chromadb
client = chromadb.PersistentClient(path='$SOURCE_DIR')
col = client.get_or_create_collection('dishes')
print(f'Current ChromaDB: {col.count()} embeddings')
"

rm -rf "$SNAPSHOT_DIR"
cp -r "$SOURCE_DIR" "$SNAPSHOT_DIR"
echo "Snapshot written to $SNAPSHOT_DIR"
echo "Run 'fly deploy' now to ship this snapshot."
