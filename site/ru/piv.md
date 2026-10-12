---
source: piv/README.md
source_hash: eb9e61ba8e11f030fb212acf3b40cf4deb90df5a
---

# Необходимое программное обеспечение {#required-software}

## macOS {#macos}

```bash
brew install openssl libp11 opensc
```

# Настройка имён {#configure-names}

Задайте имя корневого сертификата:

```bash
export NAME_ROOT="/CN=yk.root.$(LC_ALL=C tr -dc 'a-z0-9' < /dev/urandom | head -c 16)"
```

Задайте имя промежуточного сертификата:

```bash
export NAME_IA="/CN=yk.intermediate.$(LC_ALL=C tr -dc 'a-z0-9' < /dev/urandom | head -c 16)"
```

Задайте имя сервера:

```bash
export NAME_SERVER="/CN=example.local"
```

Выведите имена:

```bash
printf '\nRoot Name:\t\t%s'       "$NAME_ROOT"
printf '\nIntermediate Name:\t%s' "$NAME_IA"
printf '\nServer Name:\t\t%s\n\n' "$NAME_SERVER"
```

# Корневой сертификат {#root-certificate}

## Подготовка конфигурации корневого сертификата {#prepare-root-configuration}

Создайте временный каталог:

```bash
cd $(mktemp -d)
```

Скопируйте [`yk.cnf`](./yk.cnf):

```bash
cp ~/git/YubiKey-Guide/piv/yk.cnf .
```

Укажите путь к совместимой версии OpenSSL:

```bash
export OPENSSL=/opt/homebrew/bin/openssl
```

Подготовьте материалы для корневого сертификата:

```bash
mkdir -p certs
touch index
$OPENSSL rand -hex 16 > serial
```

## Генерация корневого ключа {#generate-root-key}

Выберите один из следующих типов ключа:

```bash
$OPENSSL ecparam -genkey -name secp384r1 -out root.key
$OPENSSL genpkey -algorithm ed25519 -out root.key
$OPENSSL genrsa -out root.key
```

## Создание запроса на корневой сертификат {#issue-root-certificate-request}

```bash
$OPENSSL req -new \
  -config yk.cnf \
  -subj "$NAME_ROOT" \
  -key root.key \
  -out root.csr
```

## Подпись корневого сертификата {#sign-root-certificate}

```bash
local DATE_START="$(date -u -v0H -v0M -v0S '+%Y%m%d%H%M%SZ')"
local DATE_END="20500101000000Z"
$OPENSSL ca -selfsign -batch \
  -config yk.cnf \
  -extensions config_root \
  -startdate "$DATE_START" \
  -enddate "$DATE_END" \
  -keyfile root.key \
  -in root.csr \
  -out root.pem
```

## Проверка корневого сертификата {#verify-root-certificate}

```bash
$OPENSSL x509 -noout -text -in root.pem
```

## Сохранение материалов в автономном хранилище {#save-materials-offline}

TODO: скопировать файлы удостоверяющего центра в зашифрованное хранилище

# Промежуточный сертификат {#intermediate-certificate}

## Генерация промежуточного ключа {#generate-intermediate-key}

Выберите один из следующих типов ключа:

```bash
$OPENSSL ecparam -genkey -name secp384r1 -out intermediate.key
$OPENSSL genpkey -algorithm ed25519 -out intermediate.key
$OPENSSL genrsa -out intermediate.key
```

## Создание запроса на промежуточный сертификат {#issue-intermediate-certificate-request}

```bash
$OPENSSL req -new \
  -config yk.cnf \
  -subj "$NAME_IA" \
  -key intermediate.key \
  -out intermediate.csr
```

## Подпись промежуточного сертификата {#sign-intermediate-certificate}

Подпишите промежуточный сертификат сроком на два года (730 дней):

```bash
$OPENSSL ca \
  -batch \
  -config yk.cnf \
  -extensions config_intermediate \
  -days "${IA_DAYS:-730}" \
  -keyfile root.key \
  -cert root.pem \
  -in intermediate.csr \
  -out intermediate.pem
```

## Проверка промежуточного сертификата {#verify-intermediate-certificate}

```bash
$OPENSSL x509 -text -noout -in intermediate.pem
$OPENSSL verify -CAfile root.pem intermediate.pem
```

## Перенос на YubiKey {#yubikey-transfer}

## Сброс {#reset}

```bash
ykman piv info
ykman piv reset
```

## Загрузка {#load}

Слоты PIV в YubiKey:

```console
9a is for PIV Authentication
9c is for Digital Signature (PIN always checked)
9d is for Key Management
9e is for Card Authentication (PIN never checked)
```

Заполните слот `9c`:

```bash
ykman piv keys import 9c intermediate.key
ykman piv certificates import 9c intermediate.pem
```

# Сертификаты сервера {#server-certificates}

## Генерация ключа сервера {#generate-server-key}

Выберите один из следующих типов ключа:

```bash
$OPENSSL ecparam -genkey -name secp384r1 -out server.key
$OPENSSL genpkey -algorithm ed25519 -out server.key
$OPENSSL genrsa -out server.key
```

## Создание запроса на сертификат сервера {#issue-server-certificate-request}

```bash
$OPENSSL req -new \
  -config yk.cnf \
  -subj "$NAME_SERVER" \
  -key server.key \
  -out server.csr
```

## Получение промежуточного сертификата {#get-intermediate-certificate}

```bash
ykman piv certificates export 9c - > intermediate.pem
```

## Подпись сертификата сервера {#sign-server-certificate}

Подпишите сертификат сервера сроком на 99 дней с помощью YubiKey:

```bash
$OPENSSL ca \
  -batch \
  -config yk.cnf \
  -extensions config_server \
  -days "${SERVER_DAYS:-99}" \
  -cert intermediate.pem \
  -keyfile 'pkcs11:id=%02;object=SIGN%20key;type=private' \
  -in server.csr \
  -out server.pem
```

## Проверка сертификата сервера {#verify-server-certificate}

```bash
$OPENSSL x509 -text -noout -in server.pem
$OPENSSL verify \
  -CAfile root.pem \
  -untrusted intermediate.pem \
  server.pem
```

# Устранение неполадок {#troubleshooting}

Получите справку по приложению CA:

```bash
man openssl-ca
$OPENSSL ca -help
```

Просмотрите содержимое карты:

```bash
pkcs11-tool \
  --module /opt/homebrew/lib/opensc-pkcs11.so \
  --list-slots

pkcs11-tool \
  --module /opt/homebrew/lib/opensc-pkcs11.so \
  --login \
  --list-objects

pkcs11-tool \
  --module /opt/homebrew/lib/opensc-pkcs11.so \
  --login \
  --list-objects \
  --type privkey

pkcs11-tool \
  --module /opt/homebrew/lib/opensc-pkcs11.so \
  --login \
  --list-objects \
  --type privkey \
  --id 02
```
