#!/usr/bin/env bash
# Install the exact supported Hugo Extended release from official assets.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly HUGO_VERSION=0.164.0
DEST_DIR=${1:-"$HOME/.local/bin"}

for command_name in curl tar sha256sum install mktemp uname awk; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "KĻŪDA: nav atrasta komanda '$command_name'" >&2
        exit 1
    }
done

case "$(uname -m)" in
    x86_64|amd64) architecture=amd64 ;;
    aarch64|arm64) architecture=arm64 ;;
    *) echo "KĻŪDA: neatbalstīta arhitektūra: $(uname -m)" >&2; exit 2 ;;
esac

archive="hugo_extended_${HUGO_VERSION}_linux-${architecture}.tar.gz"
checksums="hugo_${HUGO_VERSION}_checksums.txt"
release="https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

download_release_assets() {
    curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
        --retry 3 --retry-all-errors \
        "$release/$archive" --output "$work/$archive" || return 1
    curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
        --retry 3 --retry-all-errors \
        "$release/$checksums" --output "$work/$checksums" || return 1
}

if download_release_assets; then
    awk -v filename="$archive" '$2 == filename { print; found=1 } END { exit !found }' \
        "$work/$checksums" > "$work/expected.sha256" || {
            echo "KĻŪDA: oficiālajā checksum failā nav $archive" >&2
            exit 1
        }
    (
        cd "$work"
        sha256sum --check expected.sha256
    )
    tar -xzf "$work/$archive" -C "$work" hugo
    mkdir -p "$DEST_DIR"
    install -m 0755 "$work/hugo" "$DEST_DIR/hugo"
else
    echo "BRĪDINĀJUMS: Hugo release asset download neizdevās; būvēju tieši v$HUGO_VERSION no avota." >&2
    command -v go >/dev/null 2>&1 || {
        echo "KĻŪDA: fallback būvei nav atrasta komanda 'go'" >&2
        exit 1
    }
    command -v cc >/dev/null 2>&1 || {
        echo "KĻŪDA: Hugo Extended fallback būvei nav atrasts C kompilators 'cc'" >&2
        exit 1
    }
    mkdir -p "$work/go-bin"
    GOBIN="$work/go-bin" CGO_ENABLED=1 \
        go install -tags extended "github.com/gohugoio/hugo@v${HUGO_VERSION}"
    mkdir -p "$DEST_DIR"
    install -m 0755 "$work/go-bin/hugo" "$DEST_DIR/hugo"
fi

version_output=$("$DEST_DIR/hugo" version)
[[ "$version_output" == *"v${HUGO_VERSION}"* && "$version_output" == *"extended"* ]] || {
    echo "KĻŪDA: instalētais Hugo neatbilst Extended v$HUGO_VERSION: $version_output" >&2
    exit 1
}
printf '%s\n' "$version_output"
