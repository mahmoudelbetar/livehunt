#!/usr/bin/env bash
# One-shot publish helper. Run AFTER the repo exists on GitHub and your SSH key is added.
#   ./push.sh <github-user-or-org> [repo-name]
set -euo pipefail
OWNER=${1:?usage: ./push.sh <github-user-or-org> [repo]}
REPO=${2:-livehunt}
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR"

# replace placeholder URLs with the real owner
sed -i "s|your-org|$OWNER|g" README.md pyproject.toml
git add README.md pyproject.toml
git commit -qm "docs: point URLs at $OWNER/$REPO" || true

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "git@github.com:$OWNER/$REPO.git"
else
  git remote add origin "git@github.com:$OWNER/$REPO.git"
fi

git branch -M main
git push -u origin main
git tag -a v0.1.0 -m "livehunt 0.1.0" 2>/dev/null || true
git push origin v0.1.0 2>/dev/null || true

echo
echo "pushed: https://github.com/$OWNER/$REPO"
echo "attach the release asset: $DIR/../dist/livehunt-0.1.0.tar.gz"
