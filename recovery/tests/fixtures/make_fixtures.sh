#!/usr/bin/env bash
# Regenerates the throwaway test keys in this folder. Outputs are committed.
# Rerun only for the profiles you need, e.g. `./make_fixtures.sh flexible`;
# rebuilding ed25519, rsa4096 or expired changes the golden plans. Passphrase: test-certify-pass

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

gen_key() { # gen_key UID PRIMARY_ALGO PRIMARY_USAGE PRIMARY_EXPIRY PASS [FAKED_TIME] → prints the fingerprint
  local -a time_opt=()
  [[ -z ${6:-} ]] || time_opt=(--faked-system-time "$6")
  printf '%s\n' "$5" | gpg --batch "${time_opt[@]}" --pinentry-mode loopback --passphrase-fd 0 \
    --quick-generate-key "$1" "$2" "$3" "$4" >/dev/null 2>&1
  gpg --with-colons --list-keys "$1" | awk -F: '/^fpr/ {print $10; exit}'
}

add_sub() { # add_sub FP ALGO USAGE EXPIRY PASS [FAKED_TIME]
  local -a time_opt=()
  [[ -z ${6:-} ]] || time_opt=(--faked-system-time "$6")
  printf '%s\n' "$5" | gpg --batch "${time_opt[@]}" --pinentry-mode loopback --passphrase-fd 0 \
    --quick-add-key "$1" "$2" "$3" "$4"
}

export_secret() { # export_secret FP PASS OUT [--armor] [--export-secret-subkeys]
  local fp=$1 pass=$2 out=$3
  shift 3
  local what=--export-secret-keys opts=()
  for a in "$@"; do
    case $a in --armor) opts+=(--armor) ;; --export-secret-subkeys) what=$a ;; esac
  done
  printf '%s\n' "$pass" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 "${opts[@]}" --yes \
    --output "$out" "$what" "$fp"
}

write_info() { # write_info FP DIR
  {
    printf 'KEY_FP=%s\nKEY_ID=%s\n' "$1" "${1: -16}"
    gpg --with-colons --list-keys "$1" |
      awk -F: '$1 == "sub" {u = $12} $1 == "fpr" && u {print "FPR_" toupper(u) "=" $10; u = ""}'
  } >"$2/key.txt"
}

fresh_home() {
  GNUPGHOME=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/ykfix-XXXXXX")
  export GNUPGHOME
}

done_home() { gpgconf --kill all; rm -rf "$GNUPGHOME"; }

make_flexible() { # the spec's sample layout: expired rsa2048 [SC] main, [E] and [S], no [A], .asc names in subfolders
  local dir="flexible/asc backup" fp kid tmp
  fresh_home
  rm -rf flexible
  mkdir -p "$dir/private" "$dir/subkeys" "$dir/stub" "$dir/pub"
  fp=$(gen_key "Recovery Test (flexible) <test@example.org>" rsa2048 cert,sign 2y "$PASS" 20200101T000000)
  add_sub "$fp" rsa2048 encrypt 2y "$PASS" 20200101T000000
  add_sub "$fp" rsa2048 sign 2y "$PASS" 20200101T000000
  kid=${fp: -16}
  export_secret "$fp" "$PASS" "$dir/private/flex.priv.asc" --armor
  export_secret "$fp" "$PASS" "$dir/subkeys/flex.sub_priv.asc" --armor --export-secret-subkeys
  tmp=$(mktemp "$GNUPGHOME/x-XXXXXX")
  export_secret "$fp" "$PASS" "$tmp"
  python3 make_stubs.py --main card --sub card "$tmp" "$dir/stub/flex.priv_stub.asc"
  export_secret "$fp" "$PASS" "$tmp" --export-secret-subkeys
  python3 make_stubs.py --main keep --sub card "$tmp" "$dir/stub/flex.sub_priv_stub.asc"
  gpg --batch --armor --output "$dir/pub/flex.pub.asc" --export "$fp"
  cp "$GNUPGHOME/openpgp-revocs.d/$fp.rev" "$dir/FLEX.rev"
  write_info "$fp" flexible
  printf 'KID=%s\n' "$kid" >>flexible/key.txt
  done_home
}

make_noauth() {
  local fp kid
  fresh_home
  rm -rf noauth
  mkdir -p noauth/export
  fp=$(gen_key "Recovery Test (noauth) <test@example.org>" ed25519 cert never "$PASS")
  add_sub "$fp" ed25519 sign 2099-12-31 "$PASS"
  add_sub "$fp" cv25519 encrypt 2099-12-31 "$PASS"
  kid=${fp: -16}
  export_secret "$fp" "$PASS" "noauth/export/secret-$kid-Certify.key" --armor
  export_secret "$fp" "$PASS" "noauth/export/secret-$kid-Subkeys.key" --armor --export-secret-subkeys
  gpg --batch --armor --output "noauth/export/public-$kid-2026-10-11.asc" --export "$fp"
  write_info "$fp" noauth
  done_home
}

three_subs() { # three_subs FP PASS
  add_sub "$1" ed25519 sign 2099-12-31 "$2"
  add_sub "$1" cv25519 encrypt 2099-12-31 "$2"
  add_sub "$1" ed25519 auth 2099-12-31 "$2"
}

make_split() { # main key secret in one file, subkeys secret in another
  local fp tmp
  fresh_home
  rm -rf split
  mkdir -p split/a-main split/b-subs
  fp=$(gen_key "Recovery Test (split) <test@example.org>" ed25519 cert never "$PASS")
  three_subs "$fp" "$PASS"
  tmp=$(mktemp "$GNUPGHOME/x-XXXXXX")
  export_secret "$fp" "$PASS" "$tmp"
  python3 make_stubs.py --main keep --sub dummy "$tmp" split/a-main/main-only.asc
  export_secret "$fp" "$PASS" split/b-subs/subs-only.asc --armor --export-secret-subkeys
  write_info "$fp" split
  done_home
}

make_unprotected() {
  local fp
  fresh_home
  rm -rf unprotected
  mkdir -p unprotected
  fp=$(gen_key "Recovery Test (unprotected) <test@example.org>" ed25519 cert never "")
  three_subs "$fp" ""
  export_secret "$fp" "" unprotected/key.asc --armor
  write_info "$fp" unprotected
  done_home
}

make_mixed() { # protected main key in one file, subkeys exported with no passphrase in another
  local fp
  fresh_home
  rm -rf mixed
  mkdir -p mixed
  fp=$(gen_key "Recovery Test (mixed) <test@example.org>" ed25519 cert never "")
  three_subs "$fp" ""
  export_secret "$fp" "" mixed/subs.asc --armor --export-secret-subkeys
  printf '%s\n' "$PASS" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 --passwd "$fp"
  export_secret "$fp!" "$PASS" mixed/main.asc --armor
  write_info "$fp" mixed
  done_home
}

make_gnupg1() { # a GnuPG 1.x home: secring.gpg is a binary transferable secret key, as GnuPG 1 stored it
  local fp
  fresh_home
  rm -rf gnupg1
  mkdir -p gnupg1
  fp=$(gen_key "Recovery Test (gnupg1) <test@example.org>" ed25519 cert never "$PASS")
  three_subs "$fp" "$PASS"
  export_secret "$fp" "$PASS" gnupg1/secring.gpg
  gpg --batch --yes --output gnupg1/pubring.gpg --export "$fp"
  write_info "$fp" gnupg1
  done_home
}

make_multikey() { # two keys in one armored file
  local fp1 fp2
  fresh_home
  rm -rf multikey
  mkdir -p multikey
  fp1=$(gen_key "Recovery Test (multi one) <one@example.org>" ed25519 cert never "$PASS")
  three_subs "$fp1" "$PASS"
  fp2=$(gen_key "Recovery Test (multi two) <two@example.org>" ed25519 cert never "$PASS")
  three_subs "$fp2" "$PASS"
  printf '%s\n' "$PASS" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 --armor --yes \
    --output multikey/keys.asc --export-secret-keys "$fp1" "$fp2"
  printf 'KEY_FP=%s\nKEY2_FP=%s\n' "$fp1" "$fp2" >multikey/key.txt
  done_home
}

build() {
  case $1 in
    ed25519) make_profile ed25519 ed25519 "ed25519 cv25519 ed25519" 2099-12-31 ;;
    rsa4096) make_profile rsa4096 rsa4096 "rsa4096 rsa4096 rsa4096" 2099-12-31 ;;
    expired) make_profile expired ed25519 "ed25519 cv25519 ed25519" 1y 20200101T000000 ;;
    flexible) make_flexible ;;
    noauth) make_noauth ;;
    split) make_split ;;
    unprotected) make_unprotected ;;
    mixed) make_mixed ;;
    gnupg1) make_gnupg1 ;;
    multikey) make_multikey ;;
    *) printf 'unknown profile: %s\n' "$1" >&2; exit 2 ;;
  esac
}

# Usage: make_fixtures.sh [PROFILE...]; without arguments every profile is rebuilt (this changes the goldens).
if (($# == 0)); then
  set -- ed25519 rsa4096 expired flexible noauth split unprotected mixed gnupg1 multikey
fi
for profile in "$@"; do build "$profile"; done
