#!/bin/bash
# Arma el paquete de una version para publicarlo en GitHub Releases (lo descarga jz-middle-actualizar):
#   dist/jz-middle-<version>.tar.gz          contenido del commit actual (git archive), carpeta jz-middle-<version>/
#   dist/jz-middle-<version>.tar.gz.sha256   hash para verificarlo (formato de sha256sum)
# Correr desde la raiz del repo, con el arbol limpio y la version ya subida en jzmiddle/__init__.py:
#   tools/empaquetar.sh
#   gh release create v<version> dist/jz-middle-<version>.tar.gz dist/jz-middle-<version>.tar.gz.sha256 --notes-file ...
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' jzmiddle/__init__.py | tr -d '\r')"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Version invalida en jzmiddle/__init__.py" >&2; exit 1; }
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Hay cambios sin commitear: el paquete tiene que salir de un commit." >&2
  exit 1
fi
mkdir -p dist
NOMBRE="jz-middle-$VERSION.tar.gz"
# --worktree-attributes no hace falta: .gitattributes del repo define los finales de linea (LF para .sh).
git archive --format=tar.gz --prefix="jz-middle-$VERSION/" -o "dist/$NOMBRE" HEAD
(cd dist && sha256sum "$NOMBRE" > "$NOMBRE.sha256")
echo "Listo: dist/$NOMBRE ($(git rev-parse --short HEAD))"
cat "dist/$NOMBRE.sha256"
