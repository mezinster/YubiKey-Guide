#!/usr/bin/env bash
# Migrate GnuPG key backups to new YubiKeys (bash implementation).
# Same options, steps, --dry-run plan and exit codes as `python3 -m ykrecover --cli`.
# See recovery/README.md.

set -o errtrace -o nounset -o pipefail -o errexit
set +x
umask 077
export LC_ALL=C

readonly EXIT_OK=0 EXIT_USAGE=1 EXIT_BACKUP=2 EXIT_CARD=3 EXIT_INTERRUPT=130
readonly DEFAULT_ADMIN_PIN=12345678 DEFAULT_USER_PIN=123456
readonly TOUCH_POLICIES="off on fixed cached cached-fixed"
readonly LOOPBACK=(gpg --batch --pinentry-mode loopback)
readonly CARD_GPG=(gpg --batch --command-fd 0 --pinentry-mode loopback)

BACKUP="" MODE=clone CARDS=1 OUT="" EXPIRE=2y ALGO="" REVOKE_OLD=0 PINS=generate SAME_PINS=0
RETRIES=5,5,5 TOUCH=on TOUCH_SIG="" TOUCH_ENC="" TOUCH_AUT="" NAME="" LOGIN="" URL=""
SOURCE="" KEY="" WORKDIR="" DRY_RUN=0 YES=0
RETRY=() HOMES_CREATED=() STEP_STDIN=() STEP_NO=0 CURRENT_STEP="" CERTIFY_PASS=""
WORK="" STAGING="" CARDHOME="" VERIFYHOME="" CHOSEN=""
KEY_FP="" KEY_ID="" HAS_SECRET=0 KEY_EXPIRES=0 KEY_ALGO="" UNPROTECTED=0 NOW=0
LOADED_FPRS=()
CAND_KIND=() CAND_PATH=() CAND_FILES=() CAND_KEY=() CAND_UNPROT=() CAND_BARE=()
KEY_FILES=() INV_UNRECOVERABLE=() INV_REPORT="" INV_FILES_FOUND=0 INV_UNREADABLE=0
UIDS=() SUB_FPR=() SUB_USAGE=() SUB_CREATED=() SUB_EXPIRES=() SUB_VALIDITY=() SUB_ALGO=() SUB_SECRET=()
declare -A TARGET=() TOKENS=()

die() { # Prints an error and exits with the given code.
  local code=$1
  shift
  printf 'error: %s\n' "$*" >&2
  exit "$code"
}

warn() { printf 'warning: %s\n' "$*" >&2; }

info() { printf '%s\n' "$*" >&2; }

usage() {
  cat >&2 <<'EOF'
usage: recoverCard.sh --backup DIR [--mode clone|renew|rotate] [--cards N] [--out DIR]
         [--expire SPEC] [--algo rsa4096|ed25519] [--revoke-old] [--pins generate|enter]
         [--same-pins] [--retries U,R,A] [--touch POLICY] [--touch-sig|--touch-enc|--touch-aut POLICY]
         [--name NAME] [--login LOGIN] [--url URL] [--source export|subkeys-only|gnupghome]
         [--key FPR] [--workdir DIR] [--dry-run] [--yes]
       recoverCard.sh update-stubs [--import FILE] [--remove-serial SERIAL ...] [--cards N]
EOF
}

bad_usage() { usage; exit "$EXIT_USAGE"; }

one_of() { # one_of VALUE "a b c"
  local v
  for v in $2; do [[ $1 == "$v" ]] && return 0; done
  return 1
}

# --- prompts (stderr), answers from stdin; EOF aborts with 130

read_line() { # read_line PROMPT VAR
  local __value
  printf '%s ' "$1" >&2
  if ! IFS= read -r __value; then printf '\n' >&2; die "$EXIT_INTERRUPT" "aborted"; fi
  printf -v "$2" '%s' "$__value"
}

ask_secret() { # ask_secret PROMPT VAR; no echo on a terminal
  local __value
  printf '%s ' "$1" >&2
  if [[ -t 0 ]]; then
    if ! IFS= read -rs __value; then printf '\n' >&2; die "$EXIT_INTERRUPT" "aborted"; fi
    printf '\n' >&2
  elif ! IFS= read -r __value; then
    printf '\n' >&2
    die "$EXIT_INTERRUPT" "aborted"
  fi
  printf -v "$2" '%s' "$__value"
}

confirm() { # confirm PROMPT → status
  local answer
  read_line "$1 [y/N]" answer
  [[ ${answer,,} == y || ${answer,,} == yes ]]
}

# --- options

parse_args() {
  while (( $# )); do
    case $1 in
      --backup | --mode | --cards | --out | --expire | --algo | --pins | --retries | --touch | \
        --touch-sig | --touch-enc | --touch-aut | --name | --login | --url | --source | --key | --workdir)
        (( $# >= 2 )) || bad_usage
        case $1 in
          --backup) BACKUP=$2 ;;
          --mode) one_of "$2" "clone renew rotate" || bad_usage; MODE=$2 ;;
          --cards) CARDS=$2 ;;
          --out) OUT=$2 ;;
          --expire) EXPIRE=$2 ;;
          --algo) one_of "$2" "rsa4096 ed25519" || bad_usage; ALGO=$2 ;;
          --pins) one_of "$2" "generate enter" || bad_usage; PINS=$2 ;;
          --retries) RETRIES=$2 ;;
          --touch) one_of "$2" "$TOUCH_POLICIES" || bad_usage; TOUCH=$2 ;;
          --touch-sig) one_of "$2" "$TOUCH_POLICIES" || bad_usage; TOUCH_SIG=$2 ;;
          --touch-enc) one_of "$2" "$TOUCH_POLICIES" || bad_usage; TOUCH_ENC=$2 ;;
          --touch-aut) one_of "$2" "$TOUCH_POLICIES" || bad_usage; TOUCH_AUT=$2 ;;
          --name) NAME=$2 ;;
          --login) LOGIN=$2 ;;
          --url) URL=$2 ;;
          --source) one_of "$2" "export subkeys-only gnupghome" || bad_usage; SOURCE=$2 ;;
          --key) KEY=$2 ;;
          --workdir) WORKDIR=$2 ;;
        esac
        shift ;;
      --revoke-old) REVOKE_OLD=1 ;;
      --same-pins) SAME_PINS=1 ;;
      --dry-run) DRY_RUN=1 ;;
      --yes) YES=1 ;;
      -h | --help) usage; exit "$EXIT_OK" ;;
      *) bad_usage ;;
    esac
    shift
  done
  [[ -n $BACKUP ]] || bad_usage
}

check_field() { # check_field FLAG VALUE LIMIT: printable ASCII without '<'
  local printable='^[ -;=-~]*$'
  if (( ${#2} > $3 )) || ! [[ $2 =~ $printable ]]; then
    die "$EXIT_USAGE" "$1 must be at most $3 printable ASCII characters without '<'"
  fi
}

validate_options() {
  local out src r
  check_field --name "$NAME" 39
  check_field --login "$LOGIN" 254
  check_field --url "$URL" 254
  if [[ -n $WORKDIR ]]; then
    [[ -d $WORKDIR ]] || die "$EXIT_USAGE" "--workdir must be an existing folder: $WORKDIR"
    WORKDIR=$(realpath -- "$WORKDIR")
    (( ${#WORKDIR} <= 60 )) ||
      die "$EXIT_USAGE" "--workdir path is too long for gpg-agent sockets (at most 60 characters): $WORKDIR"
  fi
  [[ $CARDS =~ ^[0-9]{1,4}$ ]] || die "$EXIT_USAGE" "--cards must be 1 or more"
  CARDS=$((10#$CARDS))
  (( CARDS >= 1 )) || die "$EXIT_USAGE" "--cards must be 1 or more"
  [[ $RETRIES =~ ^[0-9]+,[0-9]+,[0-9]+$ ]] || die "$EXIT_USAGE" "--retries expects U,R,A such as 5,5,5"
  IFS=, read -r -a RETRY <<<"$RETRIES"
  for r in "${RETRY[@]}"; do
    (( ${#r} <= 4 )) && (( 10#$r >= 1 && 10#$r <= 99 )) || die "$EXIT_USAGE" "retries must be three numbers from 1 to 99"
  done
  if [[ -n $ALGO || $REVOKE_OLD == 1 ]] && [[ $MODE != rotate ]]; then
    die "$EXIT_USAGE" "--algo and --revoke-old need --mode rotate"
  fi
  (( DRY_RUN )) && return 0
  [[ -n $OUT ]] || die "$EXIT_USAGE" "--out is required"
  out=$(realpath -m -- "$OUT")
  src=$(realpath -m -- "$BACKUP")
  if [[ $out == "$src" || $out == "$src"/* ]]; then die "$EXIT_USAGE" "--out must be outside the backup folder"; fi
  if [[ -d $out ]] && compgen -G "$out/secret-*.key" >/dev/null; then
    die "$EXIT_USAGE" "$out already holds key files; choose an empty folder"
  fi
}

runtime_base() {
  local base=${XDG_RUNTIME_DIR:-}
  if [[ -n $WORKDIR ]]; then printf '%s' "$WORKDIR"
  elif [[ -n $base && -d $base && ${#base} -le 60 ]]; then printf '%s' "$base"
  else printf '/tmp'; fi
}

preflight() {
  local tools=(gpg gpgconf) t missing=() ver major minor iface
  (( DRY_RUN )) || tools+=(gpg-connect-agent ykman)
  for t in "${tools[@]}"; do command -v "$t" >/dev/null || missing+=("$t"); done
  (( ${#missing[@]} == 0 )) || die "$EXIT_USAGE" "missing tools: ${missing[*]}"
  ver=$(gpg --version | awk 'NR == 1 {print $NF}')
  IFS=. read -r major minor _ <<<"$ver"
  if ! (( major > 2 || (major == 2 && minor >= 2) )); then die "$EXIT_USAGE" "gpg 2.2 or newer is required"; fi
  (( DRY_RUN )) && return 0
  for iface in /sys/class/net/*; do
    if [[ ${iface##*/} != lo && $(cat "$iface/operstate" 2>/dev/null) == up ]]; then
      warn "network interface ${iface##*/} is up; an air-gapped system is recommended"
    fi
  done
  if [[ $(stat -f -c %T "$(runtime_base)") != tmpfs ]]; then
    warn "temporary key copies are on disk at $(runtime_base); use --workdir with a RAM disk or encrypted volume to avoid that"
  fi
}

# --- throwaway GnuPG homes

new_home() { # new_home LABEL VAR; returns 1 on any failure
  local dir
  dir=$(mktemp -d "$(runtime_base)/ykr-$1-XXXXXXXX") || return 1
  [[ -n $dir && -d $dir ]] || return 1
  HOMES_CREATED+=("$dir")
  chmod 700 "$dir" || return 1
  printf -v "$2" '%s' "$dir"
}

kill_agents() { GNUPGHOME=$1 gpgconf --kill all >/dev/null 2>&1 || true; }

destroy_home() { # an empty path would make gpgconf kill the agent of the user's default home
  [[ -n $1 ]] || return 0
  kill_agents "$1"
  rm -rf -- "$1"
}

clone_home() { # clone_home SRC LABEL VAR; returns 1 on any failure
  local dst
  kill_agents "$1"
  new_home "$2" "$3" || return 1
  dst=${!3}
  [[ -n $dst && -d $dst ]] || return 1
  cp -a -- "$1/." "$dst/" || return 1
  rm -f -- "$dst"/S.* "$dst"/*.lock "$dst"/.#* "$dst"/random_seed
}

cleanup() {
  local dir
  reveal_pins
  for dir in "${HOMES_CREATED[@]}"; do
    if [[ -d $dir ]]; then destroy_home "$dir"; fi
  done
  CERTIFY_PASS=""
}

trap cleanup EXIT
trap 'exit "$EXIT_INTERRUPT"' INT TERM HUP

# --- backup detection and loading (sources are only read)

add_candidate() { # add_candidate KIND DIR KEY UNPROT BARE FILE...
  CAND_KIND+=("$1")
  CAND_PATH+=("$2")
  CAND_KEY+=("$3")
  CAND_UNPROT+=("$4")
  CAND_BARE+=("$5")
  shift 5
  local IFS=$'\n'
  CAND_FILES+=("$*")
}

looks_like_key_file() { # looks_like_key_file PATH: an armored key block or a binary key packet, at most 1 MiB
  local f=$1 head byte tag
  [[ -f $f && -r $f && ! -L $f ]] || return 1
  (( $(stat -c %s -- "$f") <= 1048576 )) || return 1
  head=$(head -c 64 -- "$f" | tr '\000' '\001')
  head=${head#$'\xef\xbb\xbf'}
  head=${head#"${head%%[![:space:]]*}"}
  if [[ $head == '-----BEGIN PGP PRIVATE KEY BLOCK-----'* || $head == '-----BEGIN PGP PUBLIC KEY BLOCK-----'* ]]; then
    return 0
  fi
  byte=$(od -An -tu1 -N1 -- "$f" | tr -d ' \n')
  [[ -n $byte ]] && (( byte & 0x80 )) || return 1
  if (( byte & 0x40 )); then tag=$((byte & 0x3F)); else tag=$(((byte >> 2) & 0x0F)); fi
  [[ $tag == 5 || $tag == 6 || $tag == 7 || $tag == 14 ]]
}

is_gnupg2_home() { [[ -d $1/private-keys-v1.d ]] && { [[ -f $1/pubring.kbx ]] || [[ -f $1/pubring.gpg ]]; }; }

walk_dir() { # walk_dir DIR DEPTH: GnuPG homes become candidates, key files go to KEY_FILES (byte order)
  local dir=$1 depth=$2 entry name home=0 f files=()
  if is_gnupg2_home "$dir"; then
    home=1
    for f in private-keys-v1.d pubring.kbx pubring.gpg trustdb.gpg; do
      if [[ -e $dir/$f ]]; then files+=("$dir/$f"); fi
    done
    add_candidate gnupghome "$dir" "" 0 "" "${files[@]}"
  fi
  for entry in "$dir"/*; do # LC_ALL=C: byte order; hidden names are not matched
    [[ -e $entry || -L $entry ]] || continue
    name=${entry##*/}
    # random_seed is random bytes that can pass the packet-tag sniff
    if [[ -L $entry || $name == random_seed ]]; then continue; fi
    if [[ -d $entry ]]; then
      if [[ $name != private-keys-v1.d ]] && (( depth < 3 )); then walk_dir "$entry" $((depth + 1)); fi
    elif (( home )) && [[ $name == pubring.kbx || $name == pubring.gpg || $name == trustdb.gpg ]]; then
      continue
    elif looks_like_key_file "$entry"; then
      KEY_FILES+=("$entry")
    fi
  done
}

readable_copy() { # readable_copy PATH HOME NAME VAR: gpg cannot parse armor that starts with a UTF-8 BOM,
  # so such a file is read from a BOM-stripped copy (mode 0600) in HOME; returns 1 when it cannot be read
  local src=$1 copy=$2/$3 bom
  bom=$(head -c 3 -- "$src" | od -An -tx1 | tr -d ' \n') || return 1
  if [[ $bom != efbbbf ]]; then printf -v "$4" '%s' "$src"; return 0; fi
  (umask 077 && tail -c +4 -- "$src" >"$copy") 2>/dev/null || return 1
  printf -v "$4" '%s' "$copy"
}

classify_file() { # classify_file PATH → lines "MAINKID KEYID ROLE STATE"; non-zero when unreadable
  local out source
  readable_copy "$1" "$WORK" bom.key source || return 1
  out=$(GNUPGHOME=$WORK gpg --batch --list-packets -- "$source" 2>/dev/null) || return 1
  awk '
    function close_part() {
      if (inpkt && kid != "") { if (state == "") state = "missing"; print main, kid, role, state }
      inpkt = 0; kid = ""
    }
    /^:/ {
      close_part()
      if ($0 ~ /^:(secret|public)( sub)? key packet:/) {
        inpkt = 1; role = ($0 ~ /^:[a-z]+ sub key packet:/) ? "sub" : "main"
        state = ($0 ~ /^:public/) ? "public" : ""
      }
      next
    }
    !inpkt { next }
    {
      t = $0; sub(/^[ \t\r\n\v\f]+/, "", t); sub(/[ \t\r\n\v\f]+$/, "", t)
      if (index(t, "gnu-divert-to-card")) state = "card-stub"
      else if (state != "") { }
      else if (index(t, "gnu-dummy")) state = "missing"
      else if (index(t, "S2K") || (t ~ /^skey\[/ && t ~ /protected\]$/)) state = "secret"
      else if (t ~ /^skey\[/ && t ~ /bits\]$/) state = "secret-unprotected"
      if (t ~ /^keyid:/) { split(t, w, /[ \t]+/); kid = w[2]; if (role == "main") main = kid }
    }
    END { close_part() }' <<<"$out" | grep . || return 1
}

rank() { case $1 in secret) printf 2 ;; secret-unprotected) printf 1 ;; *) printf 0 ;; esac; }

state_word() {
  case $1 in secret) printf secret ;; secret-unprotected) printf 'secret (no passphrase)' ;;
    missing) printf missing ;; card-stub) printf 'card stub' ;; public) printf public ;; esac
}

describe_parts() { # describe_parts "ROLE STATE KEYID"... → "main secret, 2 subkeys secret"
  local line role state n s out="" sep=""
  for line in "$@"; do
    read -r role state _ <<<"$line"
    if [[ $role == main ]]; then out+="${sep}main $(state_word "$state")"; sep=", "; fi
  done
  for s in secret secret-unprotected missing card-stub public; do
    n=0
    for line in "$@"; do
      read -r role state _ <<<"$line"
      if [[ $role == sub && $state == "$s" ]]; then n=$((n + 1)); fi
    done
    if (( n == 1 )); then out+="${sep}1 subkey $(state_word "$s")"; sep=", "
    elif (( n > 1 )); then out+="${sep}$n subkeys $(state_word "$s")"; sep=", "; fi
  done
  printf '%s' "$out"
}

select_files() { # select_files KEY: the files that supply KEY's secrets (as Python's _select: first copy wins)
  local key=$1 i line role state kid secret main_secret max r new kind unprot bare=""
  local rows=() sorted=() used=() supplied_order=()
  local -A supplied=()
  for i in "${!KEY_FILES[@]}"; do
    [[ -n ${parts_of[$i,$key]+set} ]] || continue
    secret=0 main_secret=0 max=0
    while read -r role state kid; do
      r=$(rank "$state")
      if (( r > max )); then max=$r; fi
      if (( r )); then
        secret=$((secret + 1))
        if [[ $role == main ]]; then main_secret=1; fi
      fi
    done <<<"${parts_of[$i,$key]%$'\n'}"
    rows+=("$max $secret $main_secret $i")
  done
  mapfile -t sorted < <(printf '%s\n' "${rows[@]}" | sort -k1,1nr -k2,2nr -k3,3nr -k4,4n | cut -d' ' -f4)
  for i in "${sorted[@]}"; do
    new=0
    while read -r role state kid; do # gpg keeps a key's first copy: later ones are "unchanged"
      r=$(rank "$state")
      if (( r )) && [[ -z ${supplied[$kid]+set} ]]; then
        supplied_order+=("$kid")
        supplied[$kid]=$r
        new=1
      fi
    done <<<"${parts_of[$i,$key]%$'\n'}"
    if (( new )); then used+=("$i"); fi
  done
  for i in "${sorted[@]}"; do
    if [[ " ${used[*]} " == *" $i "* ]]; then status[$i,$key]=used; continue; fi
    status[$i,$key]=ignored
    while read -r role state kid; do
      if (( $(rank "$state") )); then status[$i,$key]="not needed"; fi
    done <<<"${parts_of[$i,$key]%$'\n'}"
  done
  if (( ${#used[@]} == 0 )); then INV_UNRECOVERABLE+=("$key"); return 0; fi
  kind=subkeys-only
  if (( ${supplied[$key]:-0} )); then kind="export"; fi
  unprot=1
  for kid in "${supplied_order[@]}"; do
    if (( supplied[$kid] == 1 )); then bare+="${bare:+ }$kid"; else unprot=0; fi
  done
  local paths=()
  for i in "${used[@]}"; do paths+=("${KEY_FILES[i]}"); done
  add_candidate "$kind" "${paths[0]%/*}" "$key" "$unprot" "$bare" "${paths[@]}"
}

build_report() { # build_report ROOT: INV_REPORT as Python's backup.report() writes it
  local root=$1 i key rel
  local -a parts
  INV_REPORT=""
  (( ${#KEY_FILES[@]} )) || return 0
  INV_REPORT="Backup files in $root:"
  for i in "${!KEY_FILES[@]}"; do
    rel=${KEY_FILES[i]#"$root"/}
    if [[ -n ${unreadable[$i]+set} ]]; then
      INV_REPORT+=$'\n'"  $rel: not a readable key file (ignored)"
      continue
    fi
    for key in "${order[@]}"; do
      [[ -n ${parts_of[$i,$key]+set} ]] || continue
      mapfile -t parts <<<"${parts_of[$i,$key]%$'\n'}"
      INV_REPORT+=$'\n'"  $rel: key $key: $(describe_parts "${parts[@]}") (${status[$i,$key]})"
    done
  done
  for key in "${INV_UNRECOVERABLE[@]}"; do
    INV_REPORT+=$'\n'"  key $key: not recoverable from this folder (card stubs / public key only)"
  done
}

sort_candidates() { # by path bytes, then gnupghome, export, subkeys-only; ties keep their order
  local i n order_of=() kind=() path=() files=() key=() unprot=() bare=()
  for i in "${!CAND_KIND[@]}"; do
    case ${CAND_KIND[i]} in gnupghome) n=0 ;; export) n=1 ;; *) n=2 ;; esac
    order_of+=("${CAND_PATH[i]}"$'\t'"$n"$'\t'"$i")
  done
  (( ${#order_of[@]} )) || return 0
  while IFS=$'\t' read -r _ _ i; do
    kind+=("${CAND_KIND[i]}"); path+=("${CAND_PATH[i]}"); files+=("${CAND_FILES[i]}")
    key+=("${CAND_KEY[i]}"); unprot+=("${CAND_UNPROT[i]}"); bare+=("${CAND_BARE[i]}")
  done < <(printf '%s\n' "${order_of[@]}" | sort -t $'\t' -k1,1 -k2,2n -k3,3n)
  CAND_KIND=("${kind[@]}") CAND_PATH=("${path[@]}") CAND_FILES=("${files[@]}")
  CAND_KEY=("${key[@]}") CAND_UNPROT=("${unprot[@]}") CAND_BARE=("${bare[@]}")
}

detect() { # walks BACKUP, classifies key files, fills CAND_*, INV_REPORT and INV_FILES_FOUND
  local root=${BACKUP%/} i out main kid role state order=()
  local -A parts_of=() seen_key=() status=() unreadable=()
  [[ -d $root ]] || die "$EXIT_BACKUP" "backup folder not found: $root"
  KEY_FILES=() INV_UNRECOVERABLE=()
  walk_dir "$root" 0
  INV_FILES_FOUND=${#KEY_FILES[@]}
  INV_UNREADABLE=0
  for i in "${!KEY_FILES[@]}"; do
    if ! out=$(classify_file "${KEY_FILES[i]}"); then unreadable[$i]=1 INV_UNREADABLE=1; continue; fi
    while read -r main kid role state; do
      parts_of[$i,$main]+="$role $state $kid"$'\n'
      if [[ -z ${seen_key[$main]+set} ]]; then seen_key[$main]=1; order+=("$main"); fi
    done <<<"$out"
  done
  for main in "${order[@]}"; do
    select_files "$main"
  done
  build_report "$root"
  sort_candidates
}

choose_candidate() { # as Python's backup.choose; main reports a folder with no candidates first
  local i matches=() want=${KEY^^} k
  want=${want// /}
  for i in "${!CAND_KIND[@]}"; do
    [[ -z $SOURCE || ${CAND_KIND[i]} == "$SOURCE" ]] || continue
    k=${CAND_KEY[i]}
    if [[ -n $want && -n $k && $want != *"$k" && $k != *"$want" ]]; then continue; fi
    matches+=("$i")
  done
  if (( ${#matches[@]} == 1 )); then CHOSEN=${matches[0]}; return 0; fi
  if (( ${#matches[@]} == 0 )); then
    die "$EXIT_BACKUP" "no backup matches --source or --key; found:$(candidate_listing "${!CAND_KIND[@]}")"
  fi
  die "$EXIT_BACKUP" "several backups found; narrow --backup or pass --source or --key:$(candidate_listing "${matches[@]}")"
}

candidate_listing() { # candidate_listing INDEX...: "\n  <kind>: <path> (key <KEYID>)" per candidate
  local i
  for i in "$@"; do
    printf '\n  %s: %s' "${CAND_KIND[i]}" "${CAND_PATH[i]}"
    if [[ -n ${CAND_KEY[i]} ]]; then printf ' (key %s)' "${CAND_KEY[i]}"; fi
  done
}

list_keys() { # list_keys HOME → "sec FPR KEYID SECRET EXPIRES ALGO" | "uid TEXT" | "ssb FPR USAGE CREATED EXPIRES VALIDITY ALGO SECRET"
  GNUPGHOME=$1 gpg --batch --with-colons --with-keygrip --fixed-list-mode --list-secret-keys 2>/dev/null |
    awk -F: '
      $1 == "sec" { insec = 1; insub = 0; kid = $5; secret = ($15 == "#") ? 0 : 1
                    kexp = $7 + 0; kalgo = ($17 != "") ? $17 : "rsa" $3; next }
      $1 == "ssb" { insub = 1; insec = 0; usage = $12; created = $6 + 0; expires = $7 + 0
                    validity = $2; algo = ($17 != "") ? $17 : "rsa" $3; ssecret = ($15 == "+") ? 1 : 0; next }
      $1 == "fpr" && insec { print "sec", $10, kid, secret, kexp, kalgo; insec = 0; next }
      $1 == "fpr" && insub { print "ssb", $10, usage, created, expires, validity, algo, ssecret; insub = 0; next }
      $1 == "uid" { print "uid", $10 }'
}

load_bundle() { # load_bundle HOME: chooses the key (--key) and fills KEY_* and SUB_* arrays
  local kind a b c d e f g line current="" raw=${KEY:-${CAND_KEY[CHOSEN]:-}} want fps=() matches=() listing=""
  want=${raw^^}
  want=${want// /}
  while read -r kind a _; do
    if [[ $kind == sec ]]; then fps+=("$a"); fi
  done < <(list_keys "$1")
  (( ${#fps[@]} )) || die "$EXIT_BACKUP" "no secret keys in ${CAND_PATH[CHOSEN]}"
  for a in "${fps[@]}"; do
    if [[ -z $want || $a == *"$want" ]]; then matches+=("$a"); fi
  done
  if [[ -n $want ]] && (( ${#matches[@]} == 0 )); then die "$EXIT_BACKUP" "no key matches --key $raw"; fi
  if (( ${#matches[@]} != 1 )); then
    for a in "${matches[@]}"; do listing+=$'\n'"  $a"; done
    die "$EXIT_BACKUP" "several keys found; choose one with --key:$listing"
  fi
  KEY_FP=${matches[0]}
  UIDS=() SUB_FPR=() SUB_USAGE=() SUB_CREATED=() SUB_EXPIRES=() SUB_VALIDITY=() SUB_ALGO=() SUB_SECRET=()
  while IFS= read -r line; do
    read -r kind a b c d e f g <<<"$line"
    case $kind in
      sec) current=$a; if [[ $a == "$KEY_FP" ]]; then KEY_ID=$b; HAS_SECRET=$c; KEY_EXPIRES=$d; KEY_ALGO=$e; fi ;;
      uid) if [[ $current == "$KEY_FP" ]]; then UIDS+=("${line#uid }"); fi ;;
      ssb)
        if [[ $current == "$KEY_FP" ]]; then
          SUB_FPR+=("$a"); SUB_USAGE+=("$b"); SUB_CREATED+=("$c"); SUB_EXPIRES+=("$d")
          SUB_VALIDITY+=("$e"); SUB_ALGO+=("$f"); SUB_SECRET+=("$g")
        fi ;;
    esac
  done < <(list_keys "$1")
}

load_candidate() {
  local i=$1 f n=0 source
  if [[ ${CAND_KIND[i]} == gnupghome ]]; then
    kill_agents "$STAGING"
    while IFS= read -r f; do
      cp -R -- "$f" "$STAGING/"
    done <<<"${CAND_FILES[i]}"
    chmod -R u+rwX,go-rwx "$STAGING"
  else
    while IFS= read -r f; do
      readable_copy "$f" "$STAGING" "bom-$n.key" source || die "$EXIT_BACKUP" "cannot import $f: cannot read the file"
      n=$((n + 1))
      GNUPGHOME=$STAGING gpg --batch --import -- "$source" 2>"$WORK/import.err" ||
        die "$EXIT_BACKUP" "cannot import $f: $(cat "$WORK/import.err")"
    done <<<"${CAND_FILES[i]}"
  fi
  load_bundle "$STAGING"
}

usage_word() { case $1 in s) printf sign ;; e) printf encrypt ;; a) printf auth ;; esac; }

select_targets() { # the newest non-revoked subkey for each usage; empty when the key has none
  local u i best created
  for u in s e a; do
    best="" created=-1
    for i in "${!SUB_FPR[@]}"; do
      [[ ${SUB_USAGE[i]} == *"$u"* && ${SUB_VALIDITY[i]} != r ]] || continue
      if (( SUB_CREATED[i] > created )); then created=${SUB_CREATED[i]}; best=${SUB_FPR[i]}; fi
    done
    TARGET[$u]=$best
  done
}

unique_targets() { # unique_targets ARRAY_NAME: non-empty target fingerprints in s, e, a order without repeats
  local -n _list=$1
  local u
  _list=()
  for u in s e a; do
    [[ -n ${TARGET[$u]} ]] || continue
    if [[ " ${_list[*]} " != *" ${TARGET[$u]} "* ]]; then _list+=("${TARGET[$u]}"); fi
  done
}

missing_slots() { # the usages without a target, in s e a order
  local u
  for u in s e a; do
    if [[ -z ${TARGET[$u]} ]]; then printf '%s\n' "$u"; fi
  done
}

slot_word() { case $1 in s) printf sign ;; e) printf encrypt ;; a) printf authentication ;; esac; }

expire_at() { # expire_at EXPIRE NOW → epoch, or nothing for never / an unknown form (as modes.expire_at)
  local t=${1,,} n unit
  t=${t#"${t%%[![:space:]]*}"}
  t=${t%"${t##*[![:space:]]}"}
  if [[ $t =~ ^([0-9]+)([dwmy]?)$ ]] && (( 10#${BASH_REMATCH[1]} > 0 )); then
    n=$((10#${BASH_REMATCH[1]}))
    case ${BASH_REMATCH[2]} in '' | d) unit=86400 ;; w) unit=604800 ;; m) unit=2592000 ;; y) unit=31536000 ;; esac
    printf '%s' $(($2 + n * unit))
  elif [[ $t =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    date -u -d "$t" +%s 2>/dev/null || true
  fi
}

main_needs_extending() { # main_needs_extending NOW: the main key expired, or ends before the subkeys this run writes
  local until
  (( KEY_EXPIRES > 0 )) || return 1
  until=$(expire_at "$EXPIRE" "$1")
  (( KEY_EXPIRES <= $1 )) || [[ -z $until ]] || (( KEY_EXPIRES < until ))
}

is_bare() { [[ " ${CAND_BARE[CHOSEN]} " == *" $1 "* ]]; } # is_bare KEYID: supplied with no passphrase

target_is_bare() { # target_is_bare FPR: keytocard asks no passphrase; a renew/rotate subkey takes the main key's
  if [[ " ${LOADED_FPRS[*]} " == *" $1 "* ]]; then is_bare "${1: -16}"; else is_bare "$KEY_ID"; fi
}

check_bundle() {
  local now=$NOW f i u extra fprs=() stale=() stubs=() gaps=()
  if [[ $MODE != clone ]] && (( ! HAS_SECRET )); then
    die "$EXIT_BACKUP" "--mode $MODE needs the Certify key, but this backup only has subkeys"
  fi
  unique_targets fprs
  if [[ $MODE != rotate ]]; then # rotate writes new subkeys; the old ones are only revoked or left
    for f in "${fprs[@]}"; do
      for i in "${!SUB_FPR[@]}"; do
        if [[ ${SUB_FPR[i]} == "$f" && ${SUB_SECRET[i]} != 1 ]]; then stubs+=("$f"); fi
      done
    done
    if (( ${#stubs[@]} )); then die "$EXIT_BACKUP" "the backup holds card stubs, not secret subkeys: ${stubs[*]}"; fi
  fi
  if [[ $MODE == clone ]] && (( KEY_EXPIRES > 0 && KEY_EXPIRES <= now )); then
    die "$EXIT_BACKUP" "the main key has expired; use --mode renew or --mode rotate"
  fi
  for f in "${fprs[@]}"; do
    for i in "${!SUB_FPR[@]}"; do
      if [[ ${SUB_FPR[i]} == "$f" ]] && (( SUB_EXPIRES[i] > 0 && SUB_EXPIRES[i] <= now )); then stale+=("$f"); fi
    done
  done
  if (( ${#stale[@]} )) && [[ $MODE == clone ]]; then
    die "$EXIT_BACKUP" "subkeys have expired: ${stale[*]}; use --mode renew or --mode rotate"
  fi
  [[ $MODE == clone ]] || return 0
  mapfile -t gaps < <(missing_slots)
  if (( ${#gaps[@]} == 3 )); then
    die "$EXIT_BACKUP" "the key has no subkeys to put on a card; use --mode renew or --mode rotate"
  fi
  for u in "${gaps[@]}"; do
    extra=""
    if [[ $u == a ]]; then extra="; SSH will not work from this card"; fi
    warn "no $(slot_word "$u") subkey: that card slot stays empty$extra"
  done
}

# --- canonical steps (byte-identical to the Python plan)

substitute() { # substitute TEXT PUBLIC: replaces every <TOKEN> in one pass; values are never rescanned
  local rest=$1 out="" tok re='<[A-Z0-9_]+>'
  while [[ $rest =~ $re ]]; do
    tok=${BASH_REMATCH[0]}
    out+=${rest%%"$tok"*}
    rest=${rest#*"$tok"}
    if [[ $2 == 1 ]]; then
      case $tok in '<CERTIFY_PASS>' | '<ADMIN_PIN>' | '<USER_PIN>') out+=$tok; continue ;; esac
    fi
    if [[ -n ${TOKENS[$tok]+set} ]]; then out+=${TOKENS[$tok]}; else out+=$tok; fi
  done
  printf '%s' "$out$rest"
}

subst() { substitute "$1" 0; }

subst_public() { substitute "$1" 1; } # like subst, but secrets stay as placeholders

step() { # step PHASE HOME CHECK ARGV...; stdin lines come from STEP_STDIN
  local phase=$1 home=$2 check=$3 line rc=0
  shift 3
  if (( DRY_RUN )); then
    STEP_NO=$((STEP_NO + 1))
    printf 'STEP %s %s %s %s\n' "$STEP_NO" "$phase" "$home" "$*"
    for line in "${STEP_STDIN[@]}"; do printf 'STDIN %s\n' "$line"; done
    if [[ -n $check ]]; then printf 'CHECK %s\n' "$check"; fi
  else
    run_step "$phase" "$home" "$check" "$@" || rc=1
  fi
  STEP_STDIN=()
  return "$rc"
}

kill_step() { step "$1" "$2" '' gpgconf --kill all; }

algo_for() { # algo_for ALGO USAGE
  if [[ $1 == rsa4096 ]]; then printf rsa4096; elif [[ $2 == e ]]; then printf cv25519; else printf ed25519; fi
}

default_algo() { # --algo, else the family of the sign subkey (else encrypt, else auth, else the main key)
  local i ref=${TARGET[s]:-${TARGET[e]:-${TARGET[a]}}} algo=$KEY_ALGO
  if [[ -n $ALGO ]]; then printf '%s' "$ALGO"; return; fi
  for i in "${!SUB_FPR[@]}"; do
    if [[ ${SUB_FPR[i]} == "$ref" ]]; then algo=${SUB_ALGO[i]}; break; fi
  done
  if [[ $algo == rsa* ]]; then printf rsa4096; else printf ed25519; fi
}

mode_steps() {
  local u old algo secret=('<CERTIFY_PASS>') fprs=() gaps=()
  if [[ $MODE == renew || $MODE == rotate ]] && main_needs_extending "$NOW"; then
    STEP_STDIN=('<CERTIFY_PASS>')
    step mode '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --quick-set-expire "$KEY_FP" "$EXPIRE" || return 1
  fi
  case $MODE in
    renew)
      algo=$(default_algo)
      unique_targets fprs
      if (( ${#fprs[@]} )); then
        STEP_STDIN=('<CERTIFY_PASS>')
        step mode '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --quick-set-expire "$KEY_FP" "$EXPIRE" \
          "${fprs[@]}" || return 1
      fi
      mapfile -t gaps < <(missing_slots)
      for u in "${gaps[@]}"; do
        STEP_STDIN=('<CERTIFY_PASS>')
        step mode '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --quick-add-key "$KEY_FP" \
          "$(algo_for "$algo" "$u")" "$(usage_word "$u")" "$EXPIRE" || return 1
      done ;;
    rotate)
      algo=$(default_algo)
      for u in s e a; do
        STEP_STDIN=('<CERTIFY_PASS>')
        step mode '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --quick-add-key "$KEY_FP" \
          "$(algo_for "$algo" "$u")" "$(usage_word "$u")" "$EXPIRE" || return 1
      done
      if (( REVOKE_OLD )); then
        # the main key signs the revocation; with no passphrase gpg asks none
        if is_bare "$KEY_ID"; then secret=(); fi
        unique_targets fprs
        for old in "${fprs[@]}"; do
          kill_step mode '<STAGING>' || return 1
          STEP_STDIN=("key $old" revkey y 0 '' y "${secret[@]}" save)
          step mode '<STAGING>' '' "${LOOPBACK[@]}" --command-fd 0 --edit-key "$KEY_FP" || return 1
        done
      fi ;;
  esac
}

backup_steps() {
  [[ $MODE != clone ]] || return 0
  STEP_STDIN=('<CERTIFY_PASS>')
  step backup '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --yes --armor --output \
    "<OUT>/secret-$KEY_ID-Certify.key" --export-secret-keys "$KEY_FP" || return 1
  STEP_STDIN=('<CERTIFY_PASS>')
  step backup '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --yes --armor --output \
    "<OUT>/secret-$KEY_ID-Subkeys.key" --export-secret-subkeys "$KEY_FP" || return 1
  step backup '<STAGING>' '' gpg --batch --yes --armor --output "<OUT>/public-$KEY_ID-<DATE>.asc" \
    --export "$KEY_FP" || return 1
}

touch_for() {
  case $1 in sig) printf '%s' "${TOUCH_SIG:-$TOUCH}" ;; dec) printf '%s' "${TOUCH_ENC:-$TOUCH}" ;;
    aut) printf '%s' "${TOUCH_AUT:-$TOUCH}" ;; esac
}

card_steps() { # card_steps INDEX
  local phase="card$1" serial="<SERIAL$1>" h='<CARDHOME>' slot usage ref secret=()
  local -a holder=(admin login '<LOGIN>' '<ADMIN_PIN>')
  if [[ -n $NAME ]]; then holder+=(name '<SURNAME>' '<GIVEN>'); fi
  if [[ -n $URL ]]; then holder+=(url '<URL>'); fi
  kill_step "$phase" "$h" || return 1
  step "$phase" "$h" '' ykman --device "$serial" openpgp reset -f || return 1
  # Retries first: changing the retry counters may reset the PINs to defaults.
  kill_step "$phase" "$h" || return 1
  step "$phase" "$h" '' ykman --device "$serial" openpgp access set-retries "${RETRY[@]}" -f -a '<DEFAULT_ADMIN_PIN>' ||
    return 1
  kill_step "$phase" "$h" || return 1
  STEP_STDIN=(3 '<DEFAULT_ADMIN_PIN>' '<ADMIN_PIN>' '<ADMIN_PIN>' q)
  step "$phase" "$h" '' "${CARD_GPG[@]}" --change-pin || return 1
  kill_step "$phase" "$h" || return 1
  STEP_STDIN=(1 '<DEFAULT_USER_PIN>' '<USER_PIN>' '<USER_PIN>' q)
  step "$phase" "$h" '' "${CARD_GPG[@]}" --change-pin || return 1
  kill_step "$phase" "$h" || return 1
  STEP_STDIN=("${holder[@]}" quit)
  step "$phase" "$h" '' "${CARD_GPG[@]}" --edit-card || return 1
  for slot in 1 2 3; do
    case $slot in 1) usage=s ;; 2) usage=e ;; 3) usage=a ;; esac
    [[ -n ${TARGET[$usage]} ]] || continue
    # gpg asks no passphrase for an unprotected subkey; an extra line would be read as the Admin PIN.
    secret=('<CERTIFY_PASS>')
    if target_is_bare "${TARGET[$usage]}"; then secret=(); fi
    kill_step "$phase" "$h" || return 1
    STEP_STDIN=("key ${TARGET[$usage]}" keytocard "$slot" "${secret[@]}" '<ADMIN_PIN>' save)
    step "$phase" "$h" '' "${CARD_GPG[@]}" --edit-key "$KEY_FP" || return 1
  done
  for ref in sig dec aut; do
    case $ref in sig) usage=s ;; dec) usage=e ;; aut) usage=a ;; esac
    [[ -n ${TARGET[$usage]} ]] || continue
    kill_step "$phase" "$h" || return 1
    step "$phase" "$h" '' ykman --device "$serial" openpgp keys set-touch "$ref" "$(touch_for "$ref")" -f -a \
      '<ADMIN_PIN>' || return 1
  done
}

verify_steps() { # verify_steps INDEX
  local phase="verify$1" v='<VERIFYHOME>'
  local -a pin=(gpg --batch --pinentry-mode loopback --passphrase-fd 0 --yes)
  step "$phase" '<STAGING>' '' gpg --batch --yes --armor --output "$v/pub.asc" --export "$KEY_FP" || return 1
  step "$phase" "$v" '' gpg --batch --import "$v/pub.asc" || return 1
  step "$phase" "$v" cardstatus gpg --batch --with-colons --card-status || return 1
  if [[ -n ${TARGET[s]} ]]; then
    STEP_STDIN=('<USER_PIN>')
    step "$phase" "$v" '' "${pin[@]}" --local-user "${TARGET[s]}!" --output "$v/test.sig" --detach-sign \
      "$v/test.txt" || return 1
    step "$phase" "$v" '' gpg --batch --verify "$v/test.sig" "$v/test.txt" || return 1
  fi
  [[ -n ${TARGET[e]} ]] || return 0
  step "$phase" "$v" '' gpg --batch --yes --trust-model always --recipient "${TARGET[e]}!" --output \
    "$v/test.gpg" --encrypt "$v/test.txt" || return 1
  kill_step "$phase" "$v" || return 1
  STEP_STDIN=('<USER_PIN>')
  step "$phase" "$v" roundtrip "${pin[@]}" --output "$v/test.out" --decrypt "$v/test.gpg" || return 1
}

finish_steps() { # tries both exports; warns per failure (live run)
  local rc=0
  step finish '<STAGING>' '' gpg --batch --yes --armor --output "<OUT>/public-$KEY_ID-<DATE>.asc" \
    --export "$KEY_FP" || { rc=1; warn "export failed: $CURRENT_STEP"; }
  [[ -n ${TARGET[a]} ]] || return "$rc"
  step finish '<STAGING>' '' gpg --batch --yes --output "<OUT>/ssh-$KEY_ID.pub" --export-ssh-key \
    "${TARGET[a]}!" || { rc=1; warn "export failed: $CURRENT_STEP"; }
  return "$rc"
}

dry_run() {
  local i
  mode_steps
  backup_steps
  if [[ $MODE == rotate ]]; then TARGET=([s]='<NEW_FPR_S>' [e]='<NEW_FPR_E>' [a]='<NEW_FPR_A>'); fi
  if [[ $MODE == renew ]]; then
    TARGET[s]=${TARGET[s]:-<NEW_FPR_S>} TARGET[e]=${TARGET[e]:-<NEW_FPR_E>} TARGET[a]=${TARGET[a]:-<NEW_FPR_A>}
  fi
  for ((i = 1; i <= CARDS; i++)); do
    card_steps "$i"
    verify_steps "$i"
  done
  finish_steps
}

main() {
  if [[ ${1:-} == update-stubs ]]; then
    shift
    update_stubs "$@"
  fi
  parse_args "$@"
  validate_options
  preflight
  new_home work WORK || die "$EXIT_BACKUP" "cannot create a temporary GnuPG home in $(runtime_base)"
  new_home staging STAGING || die "$EXIT_BACKUP" "cannot create a temporary GnuPG home in $(runtime_base)"
  detect
  if [[ -n $INV_REPORT ]]; then info "$INV_REPORT"; fi
  if (( ${#CAND_KIND[@]} == 0 )); then
    if (( INV_UNREADABLE )); then die "$EXIT_BACKUP" "no usable secret keys in ${BACKUP%/} (see the report above)"; fi
    if (( INV_FILES_FOUND )); then
      die "$EXIT_BACKUP" "no secret keys in ${BACKUP%/}: only card stubs or public keys"
    fi
    die "$EXIT_BACKUP" "no backup found in ${BACKUP%/} (looked for OpenPGP key files and GnuPG homes)"
  fi
  choose_candidate
  UNPROTECTED=${CAND_UNPROT[CHOSEN]}
  load_candidate "$CHOSEN"
  LOADED_FPRS=("${SUB_FPR[@]}")
  NOW=$(date +%s)
  select_targets
  check_bundle
  if (( DRY_RUN )); then
    dry_run
    exit "$EXIT_OK"
  fi
  run_live
}

# --- live run

run_step() { # executes one canonical step; on failure CURRENT_STEP and $WORK/step.err describe it
  local phase=$1 home=$2 check=$3 dir arg line rc=0
  shift 3
  local -a args=()
  for arg in "$@"; do args+=("$(subst "$arg")"); done
  dir=$(subst "$home")
  CURRENT_STEP=$(subst_public "$*")
  if [[ $phase == card* || $phase == verify* ]]; then info "[$phase] $CURRENT_STEP"; fi
  if needs_touch "$check" "$@"; then info "Touch the YubiKey when it blinks"; fi
  if (( ${#STEP_STDIN[@]} )); then
    GNUPGHOME=$dir "${args[@]}" >"$WORK/step.out" 2>"$WORK/step.err" < <(
      for line in "${STEP_STDIN[@]}"; do printf '%s\n' "$(subst "$line")"; done
    ) || rc=$?
  else
    GNUPGHOME=$dir "${args[@]}" </dev/null >"$WORK/step.out" 2>"$WORK/step.err" || rc=$?
  fi
  (( rc == 0 )) || return 1
  if [[ $phase == card* ]]; then
    for arg in "${args[@]}"; do
      if [[ $arg == --change-pin ]]; then PINS_SET[${phase#card}]=1; fi
    done
  fi
  run_check "$check"
}

needs_touch() { # needs_touch CHECK ARGV...: the verify signature and decryption use the sig and dec keys
  local check=$1 arg
  shift
  if [[ $check == roundtrip ]]; then [[ $(touch_for dec) != off ]]; return; fi
  for arg in "$@"; do
    if [[ $arg == --detach-sign ]]; then [[ $(touch_for sig) != off ]]; return; fi
  done
  return 1
}

run_check() {
  local got want
  case $1 in
    cardstatus)
      want="${TARGET[s]^^}:${TARGET[e]^^}:${TARGET[a]^^}"
      got=$(awk -F: '$1 == "fpr" {print toupper($2) ":" toupper($3) ":" toupper($4); exit}' "$WORK/step.out")
      if [[ $got != "$want" ]]; then
        CURRENT_STEP="card fingerprints"
        printf 'card has %s, expected %s\n' "${got:-nothing}" "$want" >"$WORK/step.err"
        return 1
      fi ;;
    roundtrip)
      if ! cmp -s "$VERIFYHOME/test.out" "$VERIFYHOME/test.txt"; then
        CURRENT_STEP="decrypt round-trip"
        printf 'decrypted text differs from the original\n' >"$WORK/step.err"
        return 1
      fi ;;
  esac
}

describe_key() {
  local i u mark expires targets=" ${TARGET[s]} ${TARGET[e]} ${TARGET[a]} "
  info "Key $KEY_FP"
  for i in "${!UIDS[@]}"; do info "  uid ${UIDS[i]}"; done
  for i in "${!SUB_FPR[@]}"; do
    mark=""
    if [[ $targets == *" ${SUB_FPR[i]} "* ]]; then mark=" <- card"; fi
    expires=never
    if (( SUB_EXPIRES[i] > 0 )); then expires=$(date -d "@${SUB_EXPIRES[i]}" +%F); fi
    info "  [${SUB_USAGE[i]^^}] ${SUB_ALGO[i]} ${SUB_FPR[i]} expires $expires$mark"
  done
  for u in $(missing_slots); do info "  [${u^^}] none (clone leaves it empty; renew and rotate create one)"; done
}

check_passphrase() { # check_passphrase PASS → status
  local what=--export-secret-keys rc=0
  (( HAS_SECRET )) || what=--export-secret-subkeys
  GNUPGHOME=$STAGING gpg --batch --pinentry-mode loopback --passphrase-fd 0 --armor --yes --output /dev/null \
    "$what" "$KEY_FP" >/dev/null 2>&1 < <(printf '%s\n' "$1") || rc=$?
  kill_agents "$STAGING"
  return "$rc"
}

unlock() {
  local try
  for try in 1 2 3; do
    ask_secret "Certify key passphrase:" CERTIFY_PASS
    if check_passphrase "$CERTIFY_PASS"; then return 0; fi
    warn "wrong passphrase"
  done
  die "$EXIT_BACKUP" "wrong passphrase three times"
}

generate_pins() { # sets PAIR_ADMIN and PAIR_USER (the guide's method)
  local digits
  digits=$(LC_ALL=C tr -dc '0-9' </dev/urandom | head -c 14 || true)
  PAIR_ADMIN=${digits:0:8}
  PAIR_USER=${digits:8:6}
}

random_login() { printf 'yk.%s' "$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 16 || true)"; }

ask_pin() { # ask_pin LABEL MIN VAR
  local first second
  while true; do
    ask_secret "$1:" first
    if (( ${#first} < $2 || ${#first} > 127 )); then warn "must be $2 to 127 characters"; continue; fi
    ask_secret "Repeat $1:" second
    if [[ $first != "$second" ]]; then warn "entries differ"; continue; fi
    printf -v "$3" '%s' "$first"
    return 0
  done
}

declare -A ADMIN_PINS=() USER_PINS=() LOGINS=() SERIALS=() STATUS=() DETAIL=() PINS_SET=()
PINS_SHOWN=0
PAIR_ADMIN="" PAIR_USER="" SERIAL="" SURNAME="" GIVEN="" LISTED=() LIST_PROBLEM=""

assign_pins() {
  local i label have=0
  for ((i = 1; i <= CARDS; i++)); do
    if ! (( SAME_PINS && have )); then
      if [[ $PINS == generate ]]; then
        generate_pins
      else
        label="card $i"
        if (( SAME_PINS )); then label="all cards"; fi
        ask_pin "Admin PIN ($label)" 8 PAIR_ADMIN
        ask_pin "User PIN ($label)" 6 PAIR_USER
      fi
      have=1
    fi
    ADMIN_PINS[$i]=$PAIR_ADMIN
    USER_PINS[$i]=$PAIR_USER
    LOGINS[$i]=${LOGIN:-$(random_login)}
  done
}

split_name() { # sets SURNAME and GIVEN
  local -a parts
  read -r -a parts <<<"$1"
  SURNAME="" GIVEN=""
  if (( ${#parts[@]} )); then
    SURNAME=${parts[-1]}
    GIVEN="${parts[*]:0:${#parts[@]}-1}"
  fi
}

list_serials() { # sets LISTED to the connected serials, LIST_PROBLEM to ": <ykman error>" if listing failed
  local listing rc=0 err
  listing=$(ykman list --serials 2>"$WORK/list.err") || rc=$?
  mapfile -t LISTED < <(grep -E '^[0-9]+$' <<<"$listing" || true)
  LIST_PROBLEM=""
  if (( rc )); then
    err=$(<"$WORK/list.err")
    LIST_PROBLEM=": ${err:-ykman exited with status $rc}"
  fi
}

wait_for_card() { # wait_for_card INDEX DONE_LIST → SERIAL
  local answer
  while true; do
    read_line "Insert card $1 of $CARDS and press Enter" answer
    list_serials
    if (( ${#LISTED[@]} != 1 )); then
      warn "expected exactly one YubiKey, found ${#LISTED[@]}$LIST_PROBLEM"
    elif [[ $2 == *" ${LISTED[0]} "* ]]; then
      warn "card ${LISTED[0]} was already written in this session"
    else
      SERIAL=${LISTED[0]}
      return 0
    fi
  done
}

recheck_card() { # recheck_card INDEX SERIAL: a retry must reach the same card, and only it
  local answer found
  while true; do
    list_serials
    if (( ${#LISTED[@]} == 1 )) && [[ ${LISTED[0]} == "$2" ]]; then return 0; fi
    found=${LISTED[*]}
    warn "card $2 must be the only YubiKey connected; found ${found:-none}$LIST_PROBLEM"
    read_line "Insert card $1 of $CARDS and press Enter" answer
  done
}

confirm_reset() {
  local answer
  read_line "Type RESET $1 to erase card $1:" answer
  [[ $answer == "RESET $1" ]]
}

read_choice() { # read_choice VAR → retry | skip | abort
  local answer
  while true; do
    read_line "[r]etry, [s]kip or [a]bort?" answer
    case ${answer,,} in
      r*) printf -v "$1" retry; return 0 ;;
      s*) printf -v "$1" skip; return 0 ;;
      a*) printf -v "$1" abort; return 0 ;;
    esac
  done
}

write_card() { # write_card INDEX → status; runs with errexit suspended, so every step checks itself
  local i=$1 rc=0
  CURRENT_STEP="prepare card home"
  : >"$WORK/step.err"
  if ! { clone_home "$STAGING" card CARDHOME && new_home verify VERIFYHOME &&
    printf '%s\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >"$VERIFYHOME/test.txt"; } 2>>"$WORK/step.err"; then
    printf 'could not prepare temporary GnuPG homes\n' >>"$WORK/step.err"
    return 1
  fi
  split_name "$NAME"
  TOKENS["<SERIAL$i>"]=${SERIALS[$i]}
  TOKENS['<ADMIN_PIN>']=${ADMIN_PINS[$i]}
  TOKENS['<USER_PIN>']=${USER_PINS[$i]}
  TOKENS['<LOGIN>']=${LOGINS[$i]}
  TOKENS['<SURNAME>']=$SURNAME
  TOKENS['<GIVEN>']=$GIVEN
  TOKENS['<URL>']=$URL
  TOKENS['<CARDHOME>']=$CARDHOME
  TOKENS['<VERIFYHOME>']=$VERIFYHOME
  { card_steps "$i" && verify_steps "$i"; } || rc=1
  destroy_home "$CARDHOME"
  destroy_home "$VERIFYHOME"
  return "$rc"
}

write_cards() {
  local i choice done_list=" "
  gpgconf --kill scdaemon >/dev/null 2>&1 || true # release the card from the user's own scdaemon
  for ((i = 1; i <= CARDS; i++)); do
    wait_for_card "$i" "$done_list"
    SERIALS[$i]=$SERIAL
    done_list+="$SERIAL "
    if ! confirm_reset "$SERIAL"; then
      STATUS[$i]=SKIPPED
      DETAIL[$i]="reset not confirmed"
      continue
    fi
    while true; do
      if write_card "$i"; then
        STATUS[$i]=OK
        DETAIL[$i]=""
        info "[card $i] $SERIAL: OK"
        break
      fi
      warn "card $SERIAL failed at: $CURRENT_STEP"
      tail -c 2000 "$WORK/step.err" >&2 || true
      read_choice choice
      if [[ $choice == retry ]]; then
        recheck_card "$i" "$SERIAL"
        continue
      fi
      STATUS[$i]=FAILED
      DETAIL[$i]=$CURRENT_STEP
      if [[ -n ${PINS_SET[$i]:-} ]]; then DETAIL[$i]+=" (PINs were changed; see PINs shown)"; fi
      if [[ $choice == abort ]]; then die "$EXIT_INTERRUPT" "aborted"; fi
      break
    done
  done
}

reveal_pins() { # prints once, on every exit path (called from cleanup)
  local i
  (( ${#PINS_SET[@]} )) || return 0
  if (( PINS_SHOWN )); then return 0; fi
  PINS_SHOWN=1
  for ((i = 1; i <= CARDS; i++)); do
    if [[ -n ${PINS_SET[$i]:-} ]]; then
      printf 'Card %s: Admin PIN %s  User PIN %s\n' "${SERIALS[$i]}" "${ADMIN_PINS[$i]}" "${USER_PINS[$i]}" >&2 || true
    fi
  done
}

finish() {
  local i all_ok=1 public
  finish_steps || true
  info "Summary:"
  for ((i = 1; i <= CARDS; i++)); do
    info "card $i ${SERIALS[$i]:--} ${STATUS[$i]:-pending} ${DETAIL[$i]:-}"
    if [[ ${STATUS[$i]:-} != OK ]]; then all_ok=0; fi
  done
  public="$OUT/public-$KEY_ID-$(date +%F).asc"
  info "On your daily machine run: recoverCard.sh update-stubs --import $public --cards $CARDS"
  if (( all_ok )); then return "$EXIT_OK"; fi
  return "$EXIT_CARD"
}

run_live() {
  local rc=0
  describe_key
  if (( UNPROTECTED )); then
    warn "the backup's secret keys have no passphrase"
    CERTIFY_PASS=""
    check_passphrase "" || die "$EXIT_BACKUP" "cannot use the backup's secret keys"
  else
    unlock
  fi
  if ! (( YES )) && ! confirm "Write subkeys of $KEY_FP to $CARDS card(s)?"; then exit "$EXIT_INTERRUPT"; fi
  mkdir -p -m 700 -- "$OUT" || die "$EXIT_USAGE" "cannot create the --out folder $OUT"
  TOKENS=(['<CERTIFY_PASS>']=$CERTIFY_PASS ['<OUT>']=$OUT ['<DATE>']=$(date +%F)
    ['<DEFAULT_ADMIN_PIN>']=$DEFAULT_ADMIN_PIN ['<DEFAULT_USER_PIN>']=$DEFAULT_USER_PIN ['<STAGING>']=$STAGING)
  if ! { mode_steps && backup_steps; }; then
    die "$EXIT_BACKUP" "$MODE failed: $CURRENT_STEP: $(tail -c 2000 "$WORK/step.err")"
  fi
  if [[ $MODE != clone ]]; then
    load_bundle "$STAGING"
    select_targets
    kill_agents "$STAGING"
    if (( UNPROTECTED )); then warn "the new backup in $OUT will also have no passphrase"; fi
    until confirm "New backup written to $OUT. Saved it to offline storage?"; do
      warn "copy the new backup before writing cards"
    done
  fi
  assign_pins
  write_cards
  finish || rc=$?
  exit "$rc"
}

# --- update-stubs: the only part that touches the user's own GNUPGHOME

user_gnupg_home() { # user_gnupg_home VAR: the daily keyring as gpgconf reports it (percent-escaped), like Python
  local listed="" decoded="" byte
  listed=$(gpgconf --list-dirs homedir 2>/dev/null) || listed=""
  if [[ -n $listed ]]; then
    while [[ $listed == *%* ]]; do # only %XX is an escape, as in urllib.parse.unquote; a lone % stays
      decoded+=${listed%%\%*}
      listed=${listed#*\%}
      if [[ $listed =~ ^[[:xdigit:]]{2} ]]; then
        printf -v byte '%b' "\\x${listed:0:2}"
        decoded+=$byte
        listed=${listed:2}
      else
        decoded+=%
      fi
    done
    printf -v "$1" '%s' "$decoded$listed"
  else
    printf -v "$1" '%s' "${GNUPGHOME:-$HOME/.gnupg}"
  fi
}

update_stubs() {
  local import="" cards=1 home serial padded f dest i answer t rc=$EXIT_OK
  local learned err learn_rc=0
  local -a serials=() stubs=() moved=() left=()
  while (( $# )); do
    case $1 in
      --import) (( $# >= 2 )) || bad_usage; import=$2; shift ;;
      --remove-serial) (( $# >= 2 )) || bad_usage; serials+=("$2"); shift ;;
      --cards) (( $# >= 2 )) || bad_usage; cards=$2; shift ;;
      -h | --help) usage; exit "$EXIT_OK" ;;
      *) bad_usage ;;
    esac
    shift
  done
  [[ $cards =~ ^[0-9]{1,4}$ ]] || die "$EXIT_USAGE" "--cards must be 1 or more"
  cards=$((10#$cards))
  (( cards >= 1 )) || die "$EXIT_USAGE" "--cards must be 1 or more"
  for serial in "${serials[@]}"; do
    [[ $serial =~ ^[0-9]+$ ]] || die "$EXIT_USAGE" "--remove-serial must be all digits: $serial"
  done
  for t in gpg gpg-connect-agent; do command -v "$t" >/dev/null || die "$EXIT_USAGE" "missing tools: $t"; done
  user_gnupg_home home
  if [[ -n $import ]]; then
    err=$(gpg --batch --import "$import" 2>&1 >/dev/null) || die "$EXIT_USAGE" "import failed: $err"
  fi
  for serial in "${serials[@]}"; do
    padded=$(printf '%8s' "$serial" | tr ' ' 0)
    stubs=()
    shopt -s nullglob
    for f in "$home"/private-keys-v1.d/*.key; do
      if awk -v s="$padded" '$1 == "Token:" && length($2) >= 28 && toupper(substr($2, 21, 8)) == s {found = 1} END {exit !found}' "$f"; then
        stubs+=("$f")
      fi
    done
    shopt -u nullglob
    if (( ${#stubs[@]} == 0 )); then info "no stubs reference card $serial"; continue; fi
    confirm "Move ${#stubs[@]} stub(s) for card $serial out of the keyring?$(printf '\n  %s' "${stubs[@]##*/}")
" || continue
    moved=()
    dest=$(mktemp -d "$home/stub-backup-$(date +%Y%m%d-%H%M%S)-XXXXXX") || die "$EXIT_USAGE" "cannot create a backup directory in $home"
    for f in "${stubs[@]}"; do
      if ! mv -- "$f" "$dest/"; then
        left=()
        for t in "${stubs[@]}"; do [[ -e $t ]] && left+=("${t##*/}"); done
        die "$EXIT_USAGE" "moving stubs failed; moved: ${moved[*]:-none}; not moved: ${left[*]}"
      fi
      moved+=("${f##*/}")
    done
    info "moved to $dest"
  done
  for ((i = 1; i <= cards; i++)); do
    read_line "Insert card $i of $cards and press Enter" answer
    # gpg-connect-agent exits 0 even when a command answers ERR, so read its output too.
    learned=$(gpg-connect-agent "scd serialno" "learn --force" /bye) || learn_rc=$?
    err=$(grep -m 1 '^ERR' <<<"$learned" || true)
    if (( learn_rc )) || [[ -n $err ]]; then
      warn "learn failed for card $i: ${err:-exit status $learn_rc}"
      rc=$EXIT_CARD
    fi
    learn_rc=0
    if ! gpg --batch --card-status >&2; then
      warn "card status failed for card $i"
      rc=$EXIT_CARD
    fi
  done
  exit "$rc"
}

main "$@"
