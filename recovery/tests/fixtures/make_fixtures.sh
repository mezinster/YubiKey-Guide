#!/usr/bin/env bash
# Regenerates the throwaway test keys in this folder. Outputs are committed;
# rerun only when the fixture layout changes. Passphrase: test-certify-pass

set -o errtrace -o nounset -o pipefail -o errexit
export LC_ALL=C

cd "$(dirname "$0")"
PASS=test-certify-pass

make_profile() { # make_profile NAME PRIMARY_ALGO "SIGN ENCRYPT AUTH" EXPIRY [FAKED_TIME]
  local name=$1 primary=$2 expiry=$4 faked=${5:-} home fp kid usage
  local -a algos time_opt=()
  read -r -a algos <<<"$3"
  [[ -z $faked ]] || time_opt=(--faked-system-time "$faked")
  home=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/ykfix-XXXXXX")
  export GNUPGHOME=$home
  rm -rf "$name"
  mkdir -p "$name/export" "$name/subkeys-only" "$name/gnupghome"
  printf '%s\n' "$PASS" | gpg --batch "${time_opt[@]}" --pinentry-mode loopback \
    --passphrase-fd 0 --quick-generate-key "Recovery Test ($name) <test@example.org>" "$primary" cert never
  fp=$(gpg --with-colons --list-keys | awk -F: '/^fpr/ {print $10; exit}')
  kid=${fp: -16}
  for usage in sign encrypt auth; do
    printf '%s\n' "$PASS" | gpg --batch "${time_opt[@]}" --pinentry-mode loopback \
      --passphrase-fd 0 --quick-add-key "$fp" "${algos[0]}" "$usage" "$expiry"
    algos=("${algos[@]:1}")
  done
  printf '%s\n' "$PASS" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 --armor \
    --output "$name/export/secret-$kid-Certify.key" --export-secret-keys "$fp"
  printf '%s\n' "$PASS" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 --armor \
    --output "$name/export/secret-$kid-Subkeys.key" --export-secret-subkeys "$fp"
  gpg --batch --armor --output "$name/export/public-$kid-2026-10-11.asc" --export "$fp"
  cp "$name/export/secret-$kid-Subkeys.key" "$name/subkeys-only/"
  gpgconf --kill all
  cp -a "$home/private-keys-v1.d" "$home/pubring.kbx" "$home/trustdb.gpg" "$name/gnupghome/"
  rm -f "$name"/gnupghome/private-keys-v1.d/*.lock
  {
    printf 'KEY_FP=%s\nKEY_ID=%s\n' "$fp" "$kid"
    gpg --with-colons --list-keys "$fp" |
      awk -F: '$1 == "sub" {u = $12} $1 == "fpr" && u {print "FPR_" toupper(u) "=" $10; u = ""}'
  } >"$name/key.txt"
  rm -rf "$home"
}

make_profile ed25519 ed25519 "ed25519 cv25519 ed25519" 2099-12-31
make_profile rsa4096 rsa4096 "rsa4096 rsa4096 rsa4096" 2099-12-31
make_profile expired ed25519 "ed25519 cv25519 ed25519" 1y 20200101T000000
