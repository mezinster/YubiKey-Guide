#!/usr/bin/env bash
# Migrate GnuPG key backups to new YubiKeys (bash implementation).
# Same options, steps, --dry-run plan and exit codes as `python3 -m ykrecover --cli`.
# See recovery/README.md.

set -o errtrace -o nounset -o pipefail -o errexit
set +x
export LC_ALL=C

readonly EXIT_OK=0 EXIT_USAGE=1 EXIT_BACKUP=2 EXIT_CARD=3 EXIT_INTERRUPT=130
readonly DEFAULT_ADMIN_PIN=12345678 DEFAULT_USER_PIN=123456
readonly TOUCH_POLICIES="off on fixed cached cached-fixed"
readonly LOOPBACK=(gpg --batch --pinentry-mode loopback)
readonly CARD_GPG=(gpg --batch --command-fd 0 --pinentry-mode loopback)

BACKUP="" MODE=clone CARDS=1 OUT="" EXPIRE=2y ALGO="" REVOKE_OLD=0 PINS=generate SAME_PINS=0
RETRIES=5,5,5 TOUCH=on TOUCH_SIG="" TOUCH_ENC="" TOUCH_AUT="" NAME="" LOGIN="" URL=""
SOURCE="" KEY="" DRY_RUN=0 YES=0
RETRY=() HOMES_CREATED=() STEP_STDIN=() STEP_NO=0 CURRENT_STEP="" CERTIFY_PASS=""
WORK="" STAGING="" CARDHOME="" VERIFYHOME="" CHOSEN=""
KEY_FP="" KEY_ID="" HAS_SECRET=0
CAND_KIND=() CAND_PATH=() CAND_FILES=()
UIDS=() SUB_FPR=() SUB_USAGE=() SUB_CREATED=() SUB_EXPIRES=() SUB_VALIDITY=() SUB_ALGO=()
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
         [--key FPR] [--dry-run] [--yes]
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
        --touch-sig | --touch-enc | --touch-aut | --name | --login | --url | --source | --key)
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

validate_options() {
  local out src r
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
  if [[ -n $base && -d $base && ${#base} -le 60 ]]; then printf '%s' "$base"; else printf '/tmp'; fi
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
    warn "$(runtime_base) is not tmpfs; key material may touch disk"
  fi
}

# --- throwaway GnuPG homes

new_home() { # new_home LABEL VAR
  local dir
  dir=$(mktemp -d "$(runtime_base)/ykr-$1-XXXXXXXX")
  chmod 700 "$dir"
  HOMES_CREATED+=("$dir")
  printf -v "$2" '%s' "$dir"
}

kill_agents() { GNUPGHOME=$1 gpgconf --kill all >/dev/null 2>&1 || true; }

destroy_home() { kill_agents "$1"; rm -rf -- "$1"; }

clone_home() { # clone_home SRC LABEL VAR
  local dst
  kill_agents "$1"
  new_home "$2" "$3"
  dst=${!3}
  cp -a -- "$1/." "$dst/"
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
trap 'exit "$EXIT_INTERRUPT"' INT TERM

# --- backup detection and loading (sources are only read)

add_candidate() { # add_candidate KIND DIR FILE...
  CAND_KIND+=("$1")
  CAND_PATH+=("$2")
  shift 2
  local IFS=$'\n'
  CAND_FILES+=("$*")
}

detect_dir() {
  local dir=$1 f files=() certify=() subkeys=()
  if [[ -d $dir/private-keys-v1.d ]] && { [[ -f $dir/pubring.kbx ]] || [[ -f $dir/pubring.gpg ]]; }; then
    for f in private-keys-v1.d pubring.kbx pubring.gpg trustdb.gpg; do
      if [[ -e $dir/$f ]]; then files+=("$dir/$f"); fi
    done
    add_candidate gnupghome "$dir" "${files[@]}"
  fi
  shopt -s nullglob
  certify=("$dir"/secret-*-Certify.key)
  subkeys=("$dir"/secret-*-Subkeys.key)
  shopt -u nullglob
  if (( ${#certify[@]} )); then
    add_candidate export "$dir" "${certify[@]}"
  elif (( ${#subkeys[@]} )); then
    add_candidate subkeys-only "$dir" "${subkeys[@]}"
  fi
}

detect() {
  local root=${BACKUP%/} sub
  [[ -d $root ]] || die "$EXIT_BACKUP" "backup folder not found: $BACKUP"
  detect_dir "$root"
  for sub in "$root"/*/; do
    sub=${sub%/}
    if [[ -d $sub && ${sub##*/} != private-keys-v1.d ]]; then detect_dir "$sub"; fi
  done
}

choose_candidate() {
  local i matches=() listing=""
  for i in "${!CAND_KIND[@]}"; do
    if [[ -z $SOURCE || ${CAND_KIND[i]} == "$SOURCE" ]]; then matches+=("$i"); fi
  done
  if (( ${#matches[@]} == 1 )); then CHOSEN=${matches[0]}; return 0; fi
  (( ${#matches[@]} )) ||
    die "$EXIT_BACKUP" "no backup found (expected secret-*-Certify.key, secret-*-Subkeys.key or a GnuPG home)"
  for i in "${matches[@]}"; do listing+=$'\n'"  ${CAND_KIND[i]}: ${CAND_PATH[i]}"; done
  die "$EXIT_BACKUP" "several backups found; narrow --backup or pass --source:$listing"
}

list_keys() { # list_keys HOME → "sec FPR KEYID SECRET" | "uid TEXT" | "ssb FPR USAGE CREATED EXPIRES VALIDITY ALGO"
  GNUPGHOME=$1 gpg --batch --with-colons --with-keygrip --fixed-list-mode --list-secret-keys 2>/dev/null |
    awk -F: '
      $1 == "sec" { insec = 1; insub = 0; kid = $5; secret = ($15 == "#") ? 0 : 1; next }
      $1 == "ssb" { insub = 1; insec = 0; usage = $12; created = $6 + 0; expires = $7 + 0
                    validity = $2; algo = ($17 != "") ? $17 : "rsa" $3; next }
      $1 == "fpr" && insec { print "sec", $10, kid, secret; insec = 0; next }
      $1 == "fpr" && insub { print "ssb", $10, usage, created, expires, validity, algo; insub = 0; next }
      $1 == "uid" { print "uid", $10 }'
}

load_bundle() { # load_bundle HOME: chooses the key (--key) and fills KEY_* and SUB_* arrays
  local kind a b c d e f line current="" want=${KEY^^} fps=() matches=() listing=""
  want=${want// /}
  while read -r kind a _; do
    if [[ $kind == sec ]]; then fps+=("$a"); fi
  done < <(list_keys "$1")
  (( ${#fps[@]} )) || die "$EXIT_BACKUP" "no secret keys in ${CAND_PATH[CHOSEN]}"
  for a in "${fps[@]}"; do
    if [[ -z $want || $a == *"$want" ]]; then matches+=("$a"); fi
  done
  if [[ -n $want ]] && (( ${#matches[@]} == 0 )); then die "$EXIT_BACKUP" "no key matches --key $KEY"; fi
  if (( ${#matches[@]} != 1 )); then
    for a in "${matches[@]}"; do listing+=$'\n'"  $a"; done
    die "$EXIT_BACKUP" "several keys found; choose one with --key:$listing"
  fi
  KEY_FP=${matches[0]}
  UIDS=() SUB_FPR=() SUB_USAGE=() SUB_CREATED=() SUB_EXPIRES=() SUB_VALIDITY=() SUB_ALGO=()
  while IFS= read -r line; do
    read -r kind a b c d e f <<<"$line"
    case $kind in
      sec) current=$a; if [[ $a == "$KEY_FP" ]]; then KEY_ID=$b; HAS_SECRET=$c; fi ;;
      uid) if [[ $current == "$KEY_FP" ]]; then UIDS+=("${line#uid }"); fi ;;
      ssb)
        if [[ $current == "$KEY_FP" ]]; then
          SUB_FPR+=("$a"); SUB_USAGE+=("$b"); SUB_CREATED+=("$c"); SUB_EXPIRES+=("$d")
          SUB_VALIDITY+=("$e"); SUB_ALGO+=("$f")
        fi ;;
    esac
  done < <(list_keys "$1")
}

load_candidate() {
  local i=$1 f
  if [[ ${CAND_KIND[i]} == gnupghome ]]; then
    kill_agents "$STAGING"
    while IFS= read -r f; do
      cp -R -- "$f" "$STAGING/"
    done <<<"${CAND_FILES[i]}"
    chmod -R u+rwX,go-rwx "$STAGING"
  else
    while IFS= read -r f; do
      GNUPGHOME=$STAGING gpg --batch --import "$f" 2>"$WORK/import.err" ||
        die "$EXIT_BACKUP" "cannot import $f: $(cat "$WORK/import.err")"
    done <<<"${CAND_FILES[i]}"
  fi
  load_bundle "$STAGING"
}

usage_word() { case $1 in s) printf sign ;; e) printf encrypt ;; a) printf auth ;; esac; }

select_targets() { # the newest non-revoked subkey for each usage
  local u i best created
  for u in s e a; do
    best="" created=-1
    for i in "${!SUB_FPR[@]}"; do
      [[ ${SUB_USAGE[i]} == *"$u"* && ${SUB_VALIDITY[i]} != r ]] || continue
      if (( SUB_CREATED[i] > created )); then created=${SUB_CREATED[i]}; best=${SUB_FPR[i]}; fi
    done
    [[ -n $best ]] || die "$EXIT_BACKUP" "key $KEY_FP has no $(usage_word "$u") subkey"
    TARGET[$u]=$best
  done
}

unique_targets() { # unique_targets ARRAY_NAME: target fingerprints in s, e, a order without repeats
  local -n _list=$1
  local u
  _list=()
  for u in s e a; do
    if [[ " ${_list[*]} " != *" ${TARGET[$u]} "* ]]; then _list+=("${TARGET[$u]}"); fi
  done
}

check_bundle() {
  local now f i fprs=() stale=()
  if [[ $MODE != clone ]] && (( ! HAS_SECRET )); then
    die "$EXIT_BACKUP" "--mode $MODE needs the Certify key, but this backup only has subkeys"
  fi
  now=$(date +%s)
  unique_targets fprs
  for f in "${fprs[@]}"; do
    for i in "${!SUB_FPR[@]}"; do
      if [[ ${SUB_FPR[i]} == "$f" ]] && (( SUB_EXPIRES[i] > 0 && SUB_EXPIRES[i] <= now )); then stale+=("$f"); fi
    done
  done
  if (( ${#stale[@]} )) && [[ $MODE == clone ]]; then
    die "$EXIT_BACKUP" "subkeys have expired: ${stale[*]}; use --mode renew or --mode rotate"
  fi
}

# --- canonical steps (byte-identical to the Python plan)

subst() { # replaces every token in $1
  local s=$1 k
  for k in "${!TOKENS[@]}"; do s=${s//"$k"/"${TOKENS[$k]}"}; done
  printf '%s' "$s"
}

subst_public() { # like subst, but secrets stay as placeholders
  local s=$1 k
  for k in "${!TOKENS[@]}"; do
    case $k in '<CERTIFY_PASS>' | '<ADMIN_PIN>' | '<USER_PIN>') continue ;; esac
    s=${s//"$k"/"${TOKENS[$k]}"}
  done
  printf '%s' "$s"
}

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

rotate_algo() {
  local i
  if [[ -n $ALGO ]]; then printf '%s' "$ALGO"; return; fi
  for i in "${!SUB_FPR[@]}"; do
    if [[ ${SUB_FPR[i]} == "${TARGET[s]}" ]]; then
      if [[ ${SUB_ALGO[i]} == rsa* ]]; then printf rsa4096; else printf ed25519; fi
      return
    fi
  done
}

mode_steps() {
  local u old algo fprs=()
  case $MODE in
    renew)
      unique_targets fprs
      STEP_STDIN=('<CERTIFY_PASS>')
      step mode '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --quick-set-expire "$KEY_FP" "$EXPIRE" \
        "${fprs[@]}" || return 1 ;;
    rotate)
      algo=$(rotate_algo)
      for u in s e a; do
        STEP_STDIN=('<CERTIFY_PASS>')
        step mode '<STAGING>' '' "${LOOPBACK[@]}" --passphrase-fd 0 --quick-add-key "$KEY_FP" \
          "$(algo_for "$algo" "$u")" "$(usage_word "$u")" "$EXPIRE" || return 1
      done
      if (( REVOKE_OLD )); then
        unique_targets fprs
        for old in "${fprs[@]}"; do
          kill_step mode '<STAGING>' || return 1
          STEP_STDIN=("key $old" revkey y 0 '' y '<CERTIFY_PASS>' save)
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
  local phase="card$1" serial="<SERIAL$1>" h='<CARDHOME>' slot usage ref
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
    kill_step "$phase" "$h" || return 1
    STEP_STDIN=("key ${TARGET[$usage]}" keytocard "$slot" '<CERTIFY_PASS>' '<ADMIN_PIN>' save)
    step "$phase" "$h" '' "${CARD_GPG[@]}" --edit-key "$KEY_FP" || return 1
  done
  for ref in sig dec aut; do
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
  STEP_STDIN=('<USER_PIN>')
  step "$phase" "$v" '' "${pin[@]}" --local-user "${TARGET[s]}!" --output "$v/test.sig" --detach-sign \
    "$v/test.txt" || return 1
  step "$phase" "$v" '' gpg --batch --verify "$v/test.sig" "$v/test.txt" || return 1
  step "$phase" "$v" '' gpg --batch --yes --trust-model always --recipient "${TARGET[e]}!" --output \
    "$v/test.gpg" --encrypt "$v/test.txt" || return 1
  kill_step "$phase" "$v" || return 1
  STEP_STDIN=('<USER_PIN>')
  step "$phase" "$v" roundtrip "${pin[@]}" --output "$v/test.out" --decrypt "$v/test.gpg" || return 1
}

finish_steps() {
  step finish '<STAGING>' '' gpg --batch --yes --armor --output "<OUT>/public-$KEY_ID-<DATE>.asc" \
    --export "$KEY_FP" || return 1
  step finish '<STAGING>' '' gpg --batch --yes --output "<OUT>/ssh-$KEY_ID.pub" --export-ssh-key \
    "${TARGET[a]}!" || return 1
}

dry_run() {
  local i
  mode_steps
  backup_steps
  if [[ $MODE == rotate ]]; then TARGET=([s]='<NEW_FPR_S>' [e]='<NEW_FPR_E>' [a]='<NEW_FPR_A>'); fi
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
  new_home work WORK
  new_home staging STAGING
  detect
  choose_candidate
  load_candidate "$CHOSEN"
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
  local i mark expires targets=" ${TARGET[s]} ${TARGET[e]} ${TARGET[a]} "
  info "Key $KEY_FP"
  for i in "${!UIDS[@]}"; do info "  uid ${UIDS[i]}"; done
  for i in "${!SUB_FPR[@]}"; do
    mark=""
    if [[ $targets == *" ${SUB_FPR[i]} "* ]]; then mark=" <- card"; fi
    expires=never
    if (( SUB_EXPIRES[i] > 0 )); then expires=$(date -d "@${SUB_EXPIRES[i]}" +%F); fi
    info "  [${SUB_USAGE[i]^^}] ${SUB_ALGO[i]} ${SUB_FPR[i]} expires $expires$mark"
  done
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
PAIR_ADMIN="" PAIR_USER="" SERIAL="" SURNAME="" GIVEN=""

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

wait_for_card() { # wait_for_card INDEX DONE_LIST → SERIAL
  local answer serials=()
  while true; do
    read_line "Insert card $1 of $CARDS and press Enter" answer
    mapfile -t serials < <(ykman list --serials 2>/dev/null | grep -E '^[0-9]+$' || true)
    if (( ${#serials[@]} != 1 )); then
      warn "expected exactly one YubiKey, found ${#serials[@]}"
    elif [[ $2 == *" ${serials[0]} "* ]]; then
      warn "card ${serials[0]} was already written in this session"
    else
      SERIAL=${serials[0]}
      return 0
    fi
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
  clone_home "$STAGING" card CARDHOME || return 1
  new_home verify VERIFYHOME || return 1
  printf '%s\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >"$VERIFYHOME/test.txt"
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
      if [[ $choice == retry ]]; then continue; fi
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
      printf 'Card %s: Admin PIN %s  User PIN %s\n' "${SERIALS[$i]}" "${ADMIN_PINS[$i]}" "${USER_PINS[$i]}" >&2
    fi
  done
}

finish() {
  local i all_ok=1 public
  finish_steps || warn "export failed: $CURRENT_STEP"
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
  unlock
  if ! (( YES )) && ! confirm "Write subkeys of $KEY_FP to $CARDS card(s)?"; then exit "$EXIT_INTERRUPT"; fi
  mkdir -p -m 700 -- "$OUT"
  TOKENS=(['<CERTIFY_PASS>']=$CERTIFY_PASS ['<OUT>']=$OUT ['<DATE>']=$(date +%F)
    ['<DEFAULT_ADMIN_PIN>']=$DEFAULT_ADMIN_PIN ['<DEFAULT_USER_PIN>']=$DEFAULT_USER_PIN ['<STAGING>']=$STAGING)
  if ! { mode_steps && backup_steps; }; then
    die "$EXIT_BACKUP" "$MODE failed: $CURRENT_STEP: $(tail -c 2000 "$WORK/step.err")"
  fi
  if [[ $MODE != clone ]]; then
    load_bundle "$STAGING"
    select_targets
    kill_agents "$STAGING"
    until confirm "New backup written to $OUT. Saved it to offline storage?"; do
      warn "copy the new backup before writing cards"
    done
  fi
  assign_pins
  write_cards
  finish || rc=$?
  exit "$rc"
}

main "$@"
