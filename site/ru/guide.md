---
source: README.md
source_hash: 552897be8aca56bccd522b633f39f4a4a4d245af
---

В этом руководстве показано, как хранить учётные данные на [YubiKey](https://www.yubico.com/products/identifying-your-yubikey/). Закрытые ключи невозможно скопировать обратно с устройства; отдельный ключ сертификации ("Certify"), хранящийся без подключения к сети, нужен только для их замены или продления.

# Покупка YubiKey {#purchase-yubikey}

Выберите [модель YubiKey](https://www.yubico.com/store/compare/) с приложением OpenPGP. Модели YubiKey Security Key и YubiKey Bio не поддерживают OpenPGP и не подходят для этого руководства.

Проверьте подлинность YubiKey на [yubico.com/genuine](https://www.yubico.com/genuine/). Выберите Verify Device (Проверить устройство), коснитесь ключа по запросу и разрешите сайту определить модель устройства. Это помогает обнаружить некоторые случаи [вмешательства в цепочку поставок](https://media.defcon.org/DEF%20CON%2025/DEF%20CON%2025%20presentations/DEF%20CON%2025%20-%20r00killah-and-securelyfitz-Secure-Tokin-and-Doobiekeys.pdf).

Подготовьте как минимум два USB-накопителя или две карты microSD для хранения зашифрованных резервных копий без подключения к сети в разных физических местах.

# Подготовка среды {#prepare-setup-environment}

Создавайте ключи и резервные копии в отдельной среде с усиленной защитой.

Ниже среды перечислены в порядке от наименее к наиболее защищённой для этой задачи:

1. Общедоступный, общий или любой другой чужой компьютер
1. Операционная система для повседневного личного использования с неограниченным доступом к сети
1. Виртуальная система с ограниченными возможностями (например, на базе [virt-manager](https://virt-manager.org/), VirtualBox или VMware)
1. Отдельная установка [Debian](https://www.debian.org/) или [OpenBSD](https://www.openbsd.org/) с усиленной защитой
1. Временная live-среда [Debian Live](https://www.debian.org/CD/live/) или [Tails](https://tails.boum.org/index.en.html), загруженная без подключённого основного накопителя
1. Оборудование и прошивка с усиленной защитой (например, [Coreboot](https://www.coreboot.org/), [удалённый Intel ME](https://github.com/corna/me_cleaner))
1. Изолированная от сети система без сетевых возможностей, желательно Raspberry Pi на базе ARM или другой аналог с иной архитектурой

Изоляция от сети и отдельное оборудование, как правило, защищают надёжнее, чем подключённая к сети система для повседневного использования. Для большинства людей разумный базовый вариант — загрузить Debian Live с USB-накопителя на личном компьютере.

## Загрузка и проверка Debian {#download-and-verify-debian}

Скачайте последний live-образ Debian и файлы подписи. Эти команды скачивают файл контрольных сумм, его подпись и текущий 64-разрядный ISO-образ Live с XFCE:

```bash
imageUrl="https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid/"
curl -sfL -O "$imageUrl/SHA512SUMS" -O "$imageUrl/SHA512SUMS.sign"
curl -sfLO "$imageUrl/$(awk '/xfce\.iso$/ {print $NF}' SHA512SUMS)"
```

Скачайте открытый ключ подписи Debian:

```bash
gpg --keyserver hkps://keyring.debian.org \
    --recv DF9B9C49EAA9298432589D76DA87E80D6294BE9B
```

Если получить открытый ключ не удаётся, используйте другой сервер ключей или DNS-сервер:

```bash
gpg --keyserver hkps://keyserver.ubuntu.com:443 \
    --recv DF9B9C49EAA9298432589D76DA87E80D6294BE9B
```

Открытый ключ подписи Debian Live также можно импортировать из папки [pubkeys](https://github.com/drduh/YubiKey-Guide/tree/main/pubkeys):

```bash
gpg --import pubkeys/debian-DA87E80D6294BE9B.asc
```

Проверьте подпись:

```bash
gpg --verify SHA512SUMS.sign SHA512SUMS
```

Убедитесь, что отпечаток совпадает с отпечатком, опубликованным Debian:

```console
gpg: Good signature from "Debian CD signing key <debian-cd@lists.debian.org>"
```

Убедитесь, что хеш образа совпадает с указанным в подписанном файле контрольных сумм:

```bash
grep $(sha512sum debian-live-*-amd64-xfce.iso) SHA512SUMS
```

Подробнее см. [Verifying authenticity of Debian CDs](https://www.debian.org/CD/verify) (Проверка подлинности компакт-дисков Debian).

## Создание загрузочного USB-накопителя {#create-bootable-usb-device}

Подключите накопитель и определите путь к устройству. В этом руководстве везде используется `/dev/sdc`, но в вашей системе это значение может отличаться.

> [!WARNING]
> Следующие команды `dd` стирают и перезаписывают выбранное устройство. Проверьте путь к устройству, прежде чем продолжить!

**Linux**

```console
$ sudo dmesg | tail
usb-storage 3-2:1.0: USB Mass Storage device detected
sd 2:0:0:0: [sdc] Attached SCSI removable disk
```

Скопируйте образ Debian на устройство:

```bash
sudo dd if=debian-live-*-amd64-xfce.iso of=/dev/sdc bs=4M status=progress ; sync
```

**OpenBSD**

```console
$ dmesg | tail -n2
sd2 at scsibus4 targ 1 lun 0: <TS-RDF5, SD Transcend, TS3A> SCSI4 0/direct removable serial.0000000000000
sd2: 15193MB, 512 bytes/sector, 31116288 sectors

$ doas dd if=debian-live-*-amd64-xfce.iso of=/dev/rsd2c bs=4m
465+1 records in
465+1 records out
1951432704 bytes transferred in 139.125 secs (14026448 bytes/sec)
```

## Подготовка оборудования {#prepare-hardware}

Выключите компьютер, на котором будут создаваться ключи.

Отключите внутренние накопители и ненужные периферийные устройства, например беспроводную сетевую карту.

# Установка программного обеспечения {#install-software}

Загрузите компьютер с USB-накопителя Debian Live.

Настройте сеть, чтобы установить необходимое программное обеспечение; необязательные ограничения описаны в разделе [Сетевые аспекты](#network-considerations).

> [!TIP]
> Если экран Debian Live заблокируется, разблокируйте его с учётными данными `user` / `live`

Откройте терминал и установите необходимое программное обеспечение.

**Debian/Ubuntu**

```bash
sudo apt update
sudo apt -y upgrade
sudo apt -y install \
  wget gnupg2 gnupg-agent dirmngr \
  cryptsetup scdaemon pcscd \
  yubikey-personalization yubikey-manager
```

**OpenBSD**

```bash
doas pkg_add gnupg pcsc-tools
```

**macOS**

Скачайте и установите [Homebrew](https://brew.sh/) и следующие пакеты:

```bash
brew install gnupg yubikey-personalization ykman pinentry-mac wget
```

Или установите следующие пакеты с помощью [MacPorts](https://www.macports.org/install.php):

```bash
sudo port install gnupg2 yubikey-manager pinentry wget
```

**NixOS**

Вместо Debian Live используйте специально подготовленный live-образ ISO из этого репозитория. Скопируйте полученный ISO-образ на USB-накопитель, загрузитесь с него и не подключайте систему к сети, пока создаёте ключи.

```bash
ref=$(git ls-remote https://github.com/drduh/Yubikey-Guide refs/heads/main | awk '{print $1}')
nix build --experimental-features "nix-command flakes" \
  github:drduh/YubiKey-Guide/$ref?dir=nix#nixosConfigurations.yubikeyLive.x86_64-linux.config.system.build.isoImage
```

Если у вас есть локальная копия этого репозитория:

Рекомендуется, но необязательно: обновите `nixpkgs` и `drduh/config`:

```bash
nix flake update --commit-lock-file
```

Соберите образ:

```bash
nix build --experimental-features "nix-command flakes" nix#nixosConfigurations.yubikeyLive.x86_64-linux.config.system.build.isoImage
```

Скопируйте его на USB-накопитель:

```bash
sudo cp -v result/iso/yubikeyLive.iso /dev/sdc ; sync
```

Пропустите шаги по созданию временного рабочего каталога и конфигурации с усиленной защитой: они уже включены в образ.

Тестовые сборки проверяйте с помощью средств виртуализации, например QEMU. Виртуальная машина обеспечивает меньшую изоляцию, чем загруженная live-система. Чтобы проверить образ в QEMU с 4 ГиБ оперативной памяти, двумя процессорами и включённым KVM:

```bash
qemu-system-x86_64 -enable-kvm -m 4G -smp 2 \
  -drive readonly=on,media=cdrom,format=raw,file=result/iso/yubikeyLive.iso
```

**Arch Linux**

```bash
sudo pacman -Syu --needed gnupg pcsclite ccid yubikey-personalization
```

**Red Hat Enterprise Linux (RHEL)**

```bash
sudo yum install -y gnupg2 pinentry pcsc-lite pcsc-lite-libs gnupg2-smime
```

**Fedora**

```bash
sudo dnf install --skip-unavailable \
  wget gnupg2 \
  cryptsetup gnupg2-scdaemon pcsc-lite \
  yubikey-personalization-gui yubikey-manager
```

# Подготовка GnuPG {#prepare-gnupg}

Создайте временный каталог для [конфигурации GnuPG](https://www.gnupg.org/documentation/manuals/gnupg/Configuration-Options.html):

```bash
export GNUPGHOME=$(mktemp -d "${TMPDIR:-/tmp}/$(date +%Y.%m.%d)-XXXXXXXX")
printf "\nTemporary directory:\t%s\n\n" "$GNUPGHOME"
```

> [!NOTE]
> Этот каталог очищается при перезагрузке, только если он находится в файловой системе в оперативной памяти, например [tmpfs](https://en.wikipedia.org/wiki/Tmpfs).
>
> Если он находится на постоянном диске, удалите его после завершения работы с руководством командой `rm -rf "$GNUPGHOME"`, а затем перезагрузите компьютер.

## Конфигурация {#configuration}

Скачайте рекомендуемую [конфигурацию GnuPG](https://github.com/drduh/YubiKey-Guide/blob/main/config/gpg.conf) во временный каталог GnuPG.

```bash
wget https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/config/gpg.conf \
  -P $GNUPGHOME
```

Просмотрите конфигурацию, прежде чем её использовать. Она задаёт современные криптографические параметры по умолчанию и параметры, повышающие конфиденциальность, например скрытие идентификаторов ключей получателей:

```console
$ grep -v "^#" $GNUPGHOME/gpg.conf
personal-cipher-preferences AES256 AES192 AES
personal-digest-preferences SHA512 SHA384 SHA256
personal-compress-preferences ZLIB BZIP2 ZIP Uncompressed
default-preference-list SHA512 SHA384 SHA256 AES256 AES192 AES ZLIB BZIP2 ZIP Uncompressed
cert-digest-algo SHA512
s2k-digest-algo SHA512
s2k-cipher-algo AES256
charset utf-8
no-comments
no-emit-version
no-greeting
keyid-format 0xlong
list-options show-uid-validity
verify-options show-uid-validity
with-fingerprint
require-cross-certification
require-secmem
no-symkey-cache
armor
use-agent
throw-keyids
```

> [!IMPORTANT]
> До конца настройки сеть должна быть отключена.

## Идентификатор пользователя {#identity}

Создайте идентификатор пользователя (user ID) — открытую метку, прикрепляемую к ключу.

Если ключ не нужно публично связывать с вами, используйте случайную метку:

```bash
export IDENTITY="yk.$(LC_ALL=C tr -dc 'a-z0-9' < /dev/urandom | head -c 16)"
printf "\nIdentity:\t\t%s\n\n" "$IDENTITY"
```

Если вы планируете шифровать электронную почту или подписывать коммиты в Git, укажите в качестве метки имя и адрес электронной почты:

```bash
export IDENTITY="YubiKey User <yubikey@example.com>"
```

> [!TIP]
> Чтобы использовать символы двойных кавычек (`"`), заключите значение в одинарные кавычки:
>
> `export IDENTITY='My Identity (a.k.a. "YubiKey User") <yubikey@example.com>'`

## Алгоритм ключа {#key-algorithm}

Задайте алгоритм и длину ключа; рекомендуется RSA 4096:

```bash
export KEY_TYPE=rsa4096
```

## Срок действия {#expiration}

Ключ сертификации — это основной ключ, хранящийся без подключения к сети; срок действия для него задавать не следует.[^1]

Подключи подписи, шифрования и аутентификации будут храниться на YubiKey. Задайте подключам срок действия, чтобы утерянный или устаревший YubiKey перестал работать после этой даты. Для подключей рекомендуется срок действия два года — это баланс между безопасностью и удобством. Более длительный срок действия снижает частоту обслуживания.

> [!NOTE]
> После истечения срока действия подключа GnuPG по-прежнему может использовать его для расшифровки существующих сообщений, а SSH — для аутентификации. Однако подключ с истёкшим сроком действия не может шифровать новые данные или создавать новые подписи!

Подключи продлевают или заменяют с помощью ключа сертификации. См. раздел [Обновление ключей](#updating-keys).

Задайте дату окончания срока действия подключей:

```bash
export KEY_EXPIRATION=2028-08-01
```

Срок действия можно задать и относительно текущей даты, например два года начиная с сегодняшнего дня:

```bash
export KEY_EXPIRATION=2y
```

## Парольная фраза {#passphrase}

Создайте парольную фразу для ключа сертификации, хранящегося без подключения к сети. Эта парольная фраза защищает резервную копию ключа сертификации и нужна для управления подключами.

Рекомендуется парольная фраза только из заглавных букв и цифр: ограниченный набор символов облегчает её запись от руки и чтение.

Следующие команды создадут и выведут надёжную парольную фразу[^2]:

```bash
export CERTIFY_PASS=$(LC_ALL=C tr -dc "A-Z3-9" < /dev/urandom |
  tr -d "IOUS5" |
  head -c ${PASS_LENGTH:-24} |
  fold -w ${PASS_GROUPSIZE:-4} |
  paste -sd ${PASS_DELIMITER:--} -)
printf "\nCertify passphrase:\t%s\n\n" "$CERTIFY_PASS"
```

Чтобы изменить длину парольной фразы, символ-разделитель или размер группы, экспортируйте соответствующие переменные перед запуском команды создания парольной фразы, например:

```bash
export PASS_GROUPSIZE=6
export PASS_DELIMITER=+
export PASS_LENGTH=48
```

Запишите парольную фразу ключа сертификации на долговечный носитель и храните её отдельно от зашифрованной резервной копии с ключевым материалом либо запомните её.

Прилагаемые шаблоны [passphrase.html](https://github.com/drduh/YubiKey-Guide/blob/main/templates/passphrase.html) и [passphrase.txt](https://github.com/drduh/YubiKey-Guide/blob/main/templates/passphrase.txt) помогают записать её без ошибок.

Сохраните [HTML-файл](https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/templates/passphrase.html), откройте его в браузере для отображения и распечатайте. Для каждого символа парольной фразы отметьте соответствующий символ в очередной строке. Файл [passphrase.txt](https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/templates/passphrase.txt) также можно распечатать без браузера:

```bash
lp -d Printer-Name passphrase.txt
```

[Diceware](https://secure.research.vt.edu/diceware) — ещё один популярный способ создания запоминающихся парольных фраз.

# Создание ключа сертификации {#create-certify-key}

Ключ сертификации — это основной ключ OpenPGP: он заверяет подключи подписи, шифрования и аутентификации. Храните его без подключения к сети и используйте только в защищённой среде для создания, продления, отзыва или замены подключей.

Создайте ключ сертификации:

```bash
printf '%s' "$CERTIFY_PASS" |
  gpg --batch --passphrase-fd 0 \
      --quick-generate-key "$IDENTITY" "${KEY_TYPE-rsa4096}" \
      cert never
```

Задайте и выведите `KEY_FP` (полный отпечаток ключа) и `KEY_ID` (последние 16 символов отпечатка — более короткий идентификатор, который обычно принимает GnuPG):

```bash
export KEY_FP=$(gpg -k --with-colons $IDENTITY | awk -F: '/^fpr:/ { print $10; exit }')
export KEY_ID="${KEY_FP: -16}"
printf "\nKey ID/Fingerprint:\t%s\n%s\n\n" "$KEY_ID" "$KEY_FP"
```

<details>
<summary>Дополнительные идентификаторы пользователя (необязательно)</summary>

Пропустите этот раздел, если ключу OpenPGP не нужны [несколько открытых идентификаторов пользователя](https://github.com/drduh/YubiKey-Guide/issues/445). Например, это могут быть адреса для разных языков или разных Git-хостингов. Не добавляйте одновременно личный и рабочий адреса, если связь между этими идентификаторами снизит вашу конфиденциальность, — вместо этого используйте разные YubiKey.

Задайте массив Bash с дополнительными идентификаторами пользователя. Заключите каждый идентификатор пользователя целиком в кавычки и разделите элементы массива пробелами:

```bash
declare -a additional_uids
additional_uids=("Super Cool YubiKey 2026" "uid 1 <uid1@example.com>")
```

Добавьте дополнительные UID к идентификатору пользователя (Identity):

```bash
for uid in "${additional_uids[@]}" ; do \
  printf '%s' "$CERTIFY_PASS" |
  gpg --batch --passphrase-fd 0 \
      --pinentry-mode loopback --quick-add-uid "$KEY_FP" "$uid"
done
```

Установите для идентификаторов пользователя уровень доверия *ultimate* (абсолютное):

```bash
gpg --command-fd 0 --pinentry-mode loopback --edit-key "$KEY_FP" <<EOF
uid *
trust
5
y
save
EOF
```

</details>

# Создание подключей {#create-subkeys}

Создайте подключи подписи и шифрования:

```bash
printf '%s' "$CERTIFY_PASS" |
  gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
      --quick-add-key "$KEY_FP" "${KEY_TYPE-rsa4096}" \
      sign "${KEY_EXPIRATION-2y}"
printf '%s' "$CERTIFY_PASS" |
  gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
      --quick-add-key "$KEY_FP" "${KEY_TYPE-rsa4096}" \
      encrypt "${KEY_EXPIRATION-2y}"
```

Создайте подключ аутентификации:

> [!NOTE]
> Некоторые SSH-серверы не принимают ключи аутентификации RSA. Чтобы вместо него использовать [Ed25519](https://ed25519.cr.yp.to/), выполните следующую команду перед созданием подключа аутентификации:
>
> `export KEY_TYPE=ed25519`

```bash
printf '%s' "$CERTIFY_PASS" |
  gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
      --quick-add-key "$KEY_FP" "${KEY_TYPE-ed25519}" \
      auth "${KEY_EXPIRATION-2y}"
```

# Проверка ключей {#verify-keys}

Выведите список доступных закрытых ключей:

```bash
gpg -K
```

В выводе должны быть ключи **[C]ertify (сертификации), [S]ignature (подписи), [E]ncryption (шифрования) и [A]uthentication (аутентификации)**:

```console
sec   rsa4096/0xF0F2CFEB04341FB5 2026-08-01 [C]
      Key fingerprint = 4E2C 1FA3 372C BA96 A06A  C34A F0F2 CFEB 0434 1FB5
uid                   [ultimate] yk.pn8wgfx67khlhext
ssb   rsa4096/0xB3CD10E502E19637 2026-08-01 [S] [expires: 2028-08-01]
ssb   rsa4096/0x30CBE8C4B085B9F7 2026-08-01 [E] [expires: 2028-08-01]
ssb   rsa4096/0xAD9E24E1B8CB9600 2026-08-01 [A] [expires: 2028-08-01]
```

# Резервное копирование ключей {#backup-keys}

Экспортируйте открытый ключ:

```bash
gpg --output $GNUPGHOME/public-$KEY_ID-$(date +%F).asc \
    --armor --export $KEY_ID
```

Экспортируйте ключ сертификации:

```bash
printf '%s' "$CERTIFY_PASS" |
  gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
      --output $GNUPGHOME/secret-$KEY_ID-Certify.key \
      --armor --export-secret-keys $KEY_ID
```

Экспортируйте подключи:

```bash
printf '%s' "$CERTIFY_PASS" |
  gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
      --output $GNUPGHOME/secret-$KEY_ID-Subkeys.key \
      --armor --export-secret-subkeys $KEY_ID
```

> [!IMPORTANT]
> Экспортированные файлы `.key` содержат закрытые ключи. Любой, кто получит эти файлы и парольную фразу ключа сертификации, сможет создавать подключи, а также подписывать данные и расшифровывать данные, зашифрованные для этих ключей.

Создайте резервную копию на зашифрованном носителе; храните её без подключения к сети в безопасном и надёжном месте.

Повторите процедуру несколько раз на нескольких переносных накопителях, так как со временем они могут выйти из строя. В качестве дополнительной меры резервного копирования [Paperkey](https://www.jabberwocky.com/software/paperkey/) позволяет создать физическую копию ключевого материала для большей долговечности.

> [!TIP]
> Файловые системы [ext2](https://en.wikipedia.org/wiki/Ext2) (без шифрования) можно монтировать в Linux и OpenBSD.
> Для совместимости с macOS и Windows используйте вместо неё файловые системы [FAT32](https://en.wikipedia.org/wiki/Fat32) или [NTFS](https://en.wikipedia.org/wiki/Ntfs).

> [!WARNING]
> Проверьте устройство назначения (`of`), прежде чем выполнять команды `dd`: они уничтожают данные! В этом руководстве используется `/dev/sdc` — в вашей системе это значение может отличаться.

**Linux**

Подключите переносной накопитель и проверьте его имя — в этом примере `/dev/sdc`:

```console
$ sudo dmesg | tail
usb-storage 3-2:1.0: USB Mass Storage device detected
sd 2:0:0:0: [sdc] Attached SCSI removable disk

$ sudo fdisk -l /dev/sdc
Disk /dev/sdc: 14.9 GiB, 15931539456 bytes, 31116288 sectors
```

Очистите первые 4 МиБ, чтобы операционная система не сохранила прежние таблицы разделов:

```bash
sudo dd if=/dev/zero of=/dev/sdc bs=4M count=1
```

Извлеките и снова подключите накопитель, чтобы операционная система прочитала очищенную таблицу разделов перед следующей командой.

Создайте новую таблицу разделов GPT:

```bash
sudo fdisk /dev/sdc <<EOF
g
w
EOF
```

Создайте небольшой раздел (не менее 20 МБ с учётом заголовка LUKS) для хранения резервных копий:

```bash
sudo fdisk /dev/sdc <<EOF
n


+20M
w
EOF
```

Зашифруйте раздел для резервных копий с помощью [LUKS](https://dys2p.com/en/2023-05-luks-security.html), прежде чем сохранять на нём ключ сертификации.

Создайте ещё одну уникальную [парольную фразу](#passphrase) (отличную от парольной фразы ключа сертификации) для защиты зашифрованного тома:

```bash
export LUKS_PASS=$(LC_ALL=C tr -dc "A-Z3-9" < /dev/urandom |
  tr -d "IOUS5" |
  head -c ${PASS_LENGTH:-24} |
  fold -w ${PASS_GROUPSIZE:-4} |
  paste -sd ${PASS_DELIMITER:--} -)
printf "\nLUKS passphrase:\t%s\n\n" "$LUKS_PASS"
```

Эта парольная фраза открывает зашифрованный том с резервной копией, содержащий ключ сертификации. Она нужна для продления, замены или восстановления подключей. Запишите парольную фразу или запомните её.

Убедитесь, что у вас есть права root:

```bash
sudo -v
```

Отформатируйте раздел:

```bash
printf '%s' "$LUKS_PASS" | sudo cryptsetup -q luksFormat /dev/sdc1
```

Разблокируйте зашифрованный раздел и создайте отображаемое устройство:

```bash
printf '%s' "$LUKS_PASS" | sudo cryptsetup -q luksOpen /dev/sdc1 gnupg-secrets
```

Создайте файловую систему ext2:

```bash
sudo mkfs.ext2 /dev/mapper/gnupg-secrets -L gnupg-$(date +%F)
```

Смонтируйте файловую систему и скопируйте на неё временный рабочий каталог GnuPG с ключевым материалом:

```bash
sudo mkdir -p /mnt/encrypted-storage
sudo mount /dev/mapper/gnupg-secrets /mnt/encrypted-storage
sudo cp -av $GNUPGHOME /mnt/encrypted-storage/
```

Отмонтируйте и закройте зашифрованный том:

```bash
sudo umount /mnt/encrypted-storage
sudo cryptsetup luksClose gnupg-secrets
```

Повторите процедуру для остальных накопителей (рекомендуется не менее двух).

**OpenBSD**

Подключите USB-диск и определите его имя:

```console
$ dmesg | grep sd.\ at
sd2 at scsibus5 targ 1 lun 0: <TS-RDF5, SD Transcend, TS37> SCSI4 0/direct removable serial.00000000000000000000
```

Выведите существующие разделы, чтобы убедиться, что это нужное устройство:

```bash
doas disklabel -h sd2
```

Создайте раздел `a` с типом файловой системы `RAID` и размером 25 мегабайт:

```console
$ doas fdisk -giy sd2
Writing MBR at offset 0.
Writing GPT.

$ doas disklabel -E sd2
Label editor (enter '?' for help at any prompt)
sd2> a a
offset: [64]
size: [31101776] 25M
FS type: [4.2BSD] RAID
sd2*> w
sd2> q
No label changes
```

Зашифруйте его с помощью bioctl, используя уникальную [парольную фразу](#passphrase):

```console
$ doas bioctl -c C -l sd2a softraid0
New passphrase:
Re-type passphrase:
softraid0: CRYPTO volume attached as sd3
```

Создайте раздел `i` на новом криптографическом томе и файловую систему:

```console
$ doas fdisk -giy sd3
Writing MBR at offset 0.
Writing GPT.

$ doas disklabel -E sd3
Label editor (enter '?' for help at any prompt)
sd3> a i
offset: [64]
size: [16001]
FS type: [4.2BSD]
sd3*> w
sd3> q
No label changes.

$ doas newfs sd3i
```

Смонтируйте файловую систему и скопируйте на неё временный каталог со связкой ключей:

```bash
doas mkdir -p /mnt/encrypted-storage
doas mount /dev/sd3i /mnt/encrypted-storage
doas cp -av $GNUPGHOME /mnt/encrypted-storage
```

Отмонтируйте и удалите зашифрованный том:

```bash
doas umount /mnt/encrypted-storage
doas bioctl -d sd3
```

Подробнее см. [OpenBSD FAQ#14](https://www.openbsd.org/faq/faq14.html#softraidCrypto).

# Экспорт открытого ключа {#export-public-key}

> [!IMPORTANT]
> На YubiKey хранятся перенесённые на него закрытые подключи. Кроме того, на каждом компьютере нужны открытый ключ и его метаданные, чтобы GnuPG мог распознать и использовать эти подключи. Без открытого ключа расшифровывать и подписывать с помощью GnuPG **невозможно**. Однако YubiKey по-прежнему можно использовать для аутентификации SSH.

Подключите ещё один переносной накопитель или создайте новый раздел на уже используемом.

**Linux**

На том же устройстве `/dev/sdc`, что и на предыдущем шаге, создайте небольшой раздел (рекомендуется не менее 20 МБ) для хранения материалов:

```bash
sudo fdisk /dev/sdc <<EOF
n


+20M
w
EOF
```

Создайте файловую систему и экспортируйте открытый ключ:

```bash
sudo mkfs.ext2 /dev/sdc2
sudo mkdir -p /mnt/public
sudo mount /dev/sdc2 /mnt/public
gpg --armor --export $KEY_ID | sudo tee /mnt/public/$KEY_ID-$(date +%F).asc
sudo chmod 0444 /mnt/public/*.asc
```

Отмонтируйте и извлеките накопитель:

```bash
sudo umount /mnt/public
```

**OpenBSD**

```console
$ doas disklabel -E sd2
Label editor (enter '?' for help at any prompt)
sd2> a b
offset: [32130]
size: [31069710] 25M
FS type: [swap] 4.2BSD
sd2*> w
sd2> q
No label changes.
```

Создайте файловую систему и экспортируйте на неё открытый ключ:

```bash
doas newfs sd2b
doas mkdir -p /mnt/public
doas mount /dev/sd2b /mnt/public
gpg --armor --export $KEY_ID | doas tee /mnt/public/$KEY_ID-$(date +%F).asc
```

Отмонтируйте и извлеките накопитель:

```bash
doas umount /mnt/public
```

# Настройка YubiKey {#configure-yubikey}

Подключите YubiKey и проверьте его состояние:

```bash
gpg --card-status
```

Если YubiKey заблокирован, выполните инструкции из раздела [Сброс YubiKey](#reset-yubikey), чтобы безвозвратно удалить все хранящиеся на нём ключи.

## Смена PIN {#change-pin}

YubiKey содержит несколько независимых [приложений](https://developers.yubico.com/). В этом руководстве используется [приложение OpenPGP](https://developers.yubico.com/PGP/), PIN которого не связаны с PIN, используемыми [PIV](https://developers.yubico.com/PIV/), FIDO и другими функциями карты.

Название | По умолчанию | Назначение
:-: | :-: | -
PIN пользователя | `123456` | повседневные операции с ключами (расшифровка, подпись, аутентификация)
PIN администратора | `12345678` | управление настройками карты (сброс PIN, смена кода сброса, добавление ключей и сведений о владельце)
Код сброса | Нет | сброс PIN ([подробнее](https://forum.yubico.com/viewtopicd01c.html?p=9055#p9055))

*PIN пользователя* должен содержать не менее 6 символов, а *PIN администратора* — не менее 8 символов. Допускается не более 127 символов ASCII. Подробнее см. [Managing PINs](https://www.gnupg.org/howtos/card-howto/en/ch03s02.html) (Управление PIN).

YubiKey ограничивает число неудачных попыток ввода PIN, что снижает риск подбора, поэтому PIN пользователя может быть коротким и удобным для регулярного использования.

Создайте удобные цифровые PIN: 8-значный PIN администратора и 6-значный PIN пользователя. Они соответствуют минимальной длине, но её можно увеличить:

```bash
PINS=$(LC_ALL=C tr -dc '0-9' < /dev/urandom | head -c 14)
export ADMIN_PIN=${PINS:0:8}
export USER_PIN=${PINS:8:6}
printf "\nAdmin PIN:\t\t%s\nUser  PIN:\t\t%s\n\n" "$ADMIN_PIN" "$USER_PIN"
```

Смените PIN администратора:

```bash
gpg --command-fd 0 --pinentry-mode loopback --change-pin <<EOF
3
12345678
$ADMIN_PIN
$ADMIN_PIN
q
EOF
```

Смените PIN пользователя:

```bash
gpg --command-fd 0 --pinentry-mode loopback --change-pin <<EOF
1
123456
$USER_PIN
$USER_PIN
q
EOF
```

Извлеките и снова вставьте YubiKey.

> [!CAUTION]
> После трёх неверных попыток ввода *PIN пользователя* он блокируется, и разблокировать его можно только с помощью *PIN администратора* или *кода сброса*. Три неверные попытки ввода *PIN администратора* или *кода сброса* уничтожают данные на YubiKey.

Увеличьте число попыток ввода PIN, если риск случайной блокировки перевешивает возросшую возможность подбора. Эта команда задаёт пять попыток для PIN пользователя, кода сброса и PIN администратора:

```bash
ykman openpgp access set-retries 5 5 5 -f -a $ADMIN_PIN
```

## Установка атрибутов {#set-attributes}

> [!IMPORTANT]
> Метаданные карты OpenPGP может прочитать без аутентификации любой, кто имеет физический доступ к YubiKey. К ним относятся серийный номер карты, отпечатки ключей, данные открытых ключей и другие атрибуты карты. Не храните в данных входа (Login data) имя, адрес электронной почты, имя учётной записи или другой персональный идентификатор, за исключением случаев, когда это сделано намеренно для возврата потерянного устройства или учёта имущества.

Задайте атрибут карты Login (данные входа). Случайная метка, создаваемая следующей командой, намеренно не позволяет установить владельца:

```bash
export CARD_ATTR_LOGIN="yk.$(LC_ALL=C tr -dc 'a-z0-9' < /dev/urandom | head -c 16)"
printf "\nLogin attribute:\t%s\n\n" "$CARD_ATTR_LOGIN"
```

Примените атрибут:

```bash
gpg --command-fd 0 --pinentry-mode loopback --edit-card <<EOF
admin
login
$CARD_ATTR_LOGIN
$ADMIN_PIN
quit
EOF
```

Проверьте атрибут:

```console
$ gpg --card-status | grep Login
Login data .......: yk.3i568zcrib5f
```

[Атрибуты смарт-карты](https://gnupg.org/howtos/card-howto/en/smartcard-howto-single.html) также можно задать с помощью `gpg --edit-card` в режиме `admin`. Чтобы увидеть доступные параметры, используйте `help`. Атрибут [login](https://www.gnupg.org/documentation/manuals/gnupg/gpg_002dcard.html) [необходим](https://github.com/drduh/YubiKey-Guide/issues/461) для переноса ключей.

# Перенос подключей {#transfer-subkeys}

> [!NOTE]
> После каждого переноса GnuPG удаляет свою пригодную для использования локальную копию закрытого ключа и сохраняет только запись о том, что подключ находится на этом YubiKey. Скопировать подключ обратно с YubiKey невозможно. Прежде чем продолжить, убедитесь, что резервные копии, хранящиеся без подключения к сети, существуют.

Для переноса ключей нужны парольная фраза ключа сертификации и PIN администратора.

## Ключ подписи {#signature-key}

Перенесите ключ подписи:

```bash
gpg --command-fd 0 --pinentry-mode loopback --edit-key $KEY_FP <<EOF
key 1
keytocard
1
$CERTIFY_PASS
$ADMIN_PIN
save
EOF
```

## Ключ шифрования {#encryption-key}

Повторите процедуру для ключа шифрования:

```bash
gpg --command-fd 0 --pinentry-mode loopback --edit-key $KEY_FP <<EOF
key 2
keytocard
2
$CERTIFY_PASS
$ADMIN_PIN
save
EOF
```

## Ключ аутентификации {#authentication-key}

Повторите процедуру для ключа аутентификации:

```bash
gpg --command-fd 0 --pinentry-mode loopback --edit-key $KEY_FP <<EOF
key 3
keytocard
3
$CERTIFY_PASS
$ADMIN_PIN
save
EOF
```

# Проверка переноса {#verify-transfer}

Убедитесь, что подключи находятся на YubiKey:

```bash
gpg -K
```

Все три подключа должны отображаться как `ssb>` — символ `>` означает, что закрытый ключ находится на смарт-карте:

```console
sec   rsa4096/0xF0F2CFEB04341FB5 2026-08-01 [C]
      Key fingerprint = 4E2C 1FA3 372C BA96 A06A  C34A F0F2 CFEB 0434 1FB5
uid                   [ultimate] yk.pn8wgfx67khlhext
ssb>  rsa4096/0xB3CD10E502E19637 2026-08-01 [S] [expires: 2028-08-01]
ssb>  rsa4096/0x30CBE8C4B085B9F7 2026-08-01 [E] [expires: 2028-08-01]
ssb>  rsa4096/0xAD9E24E1B8CB9600 2026-08-01 [A] [expires: 2028-08-01]
```

# Завершение настройки {#finish-setup}

Убедитесь, что каждый из следующих шагов успешно выполнен:

- [ ] Парольная фраза ключа сертификации запомнена или записана и хранится в безопасном и надёжном месте отдельно от зашифрованной резервной копии.
  - Чтобы снова вывести её, выполните `echo $CERTIFY_PASS`.
  - Для записи используйте [`passphrase.html`](https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/templates/passphrase.html) или [`passphrase.txt`](https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/templates/passphrase.txt).

- [ ] Парольная фраза зашифрованного тома с резервной копией запомнена или записана и хранится отдельно от этого тома.
  - Чтобы снова вывести её, выполните `echo $LUKS_PASS`.
  - Для записи используйте [`passphrase.html`](https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/templates/passphrase.html) или [`passphrase.txt`](https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/templates/passphrase.txt).

- [ ] Ключ сертификации и подключи сохранены на зашифрованных переносных накопителях, а резервные копии хранятся без подключения к сети.
  - Храните не менее двух резервных копий.
  - Храните резервные копии в разных физических местах.

- [ ] Копия открытого ключа экспортирована в место, откуда её будет легко получить позже.
  - Используйте отдельное устройство или незашифрованный раздел.

- [ ] Уникальные PIN пользователя и PIN администратора, заменившие значения по умолчанию, запомнены или записаны.
  - Чтобы снова вывести их, выполните `echo $USER_PIN $ADMIN_PIN`.
  - Для записи используйте [`passphrase.html`](https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/templates/passphrase.html) или [`passphrase.txt`](https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/templates/passphrase.txt).

- [ ] Подключи шифрования, подписи и аутентификации перенесены на YubiKey.
  - Убедитесь, что `gpg -K` показывает `ssb>` для всех трёх подключей.

- [ ] Временный рабочий каталог GnuPG очищен перезагрузкой или удалением.

# Использование YubiKey {#using-yubikey}

Создайте или проверьте локальную связку ключей GnuPG. Эта команда выводит список открытых ключей и при необходимости инициализирует `~/.gnupg`:

```bash
gpg -k
```

Импортируйте [рекомендуемую конфигурацию](https://github.com/drduh/YubiKey-Guide/blob/main/config/gpg.conf):

```bash
cd ~/.gnupg
wget https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/config/gpg.conf
```

Задайте следующий параметр. Он устраняет проблему, при которой GnuPG раз за разом просит вставить уже вставленный YubiKey:

```bash
touch scdaemon.conf
echo "disable-ccid" >>scdaemon.conf
```

Установите необходимые пакеты:

**Debian/Ubuntu**

```bash
sudo apt update
sudo apt install -y gnupg gnupg-agent scdaemon pcscd
```

**Arch**

```bash
sudo pacman -S --needed gnupg pcsc-tools
sudo systemctl enable --now pcscd.service
```

**macOS**

```bash
brew install gnupg
```

Или с помощью MacPorts

```bash
sudo port install gnupg2 pcsc-tools
```

**OpenBSD**

```bash
doas pkg_add gnupg pcsc-tools
doas rcctl enable pcscd
doas reboot
```

Смонтируйте незашифрованный том с открытым ключом:

**Debian/Ubuntu**

```bash
sudo mkdir -p /mnt/public
sudo mount /dev/sdc2 /mnt/public
```

**OpenBSD**

```bash
doas mkdir -p /mnt/public
doas mount /dev/sd3i /mnt/public
```

Импортируйте открытый ключ:

```bash
gpg --import /mnt/public/public-*.asc
```

Или скачайте открытый ключ с сервера ключей:

```bash
gpg --recv $KEY_ID
```

Или получите открытый ключ по URL, сохранённому на YubiKey:

```bash
printf "fetch" | gpg --command-fd 0 --pinentry-mode loopback --card-edit
```

Определите идентификатор ключа:

```bash
gpg -k
export KEY_ID=0xF0F2CFEB04341FB5
```

Установите абсолютное доверие (ultimate): введите `trust`, выберите вариант `5`, затем введите `quit`:

```bash
gpg --command-fd 0 --pinentry-mode loopback --edit-key $KEY_ID <<EOF
trust
5
y
save
EOF
```

Извлеките и снова вставьте YubiKey.

Проверьте состояние командой `gpg --card-status`, которая выведет список доступных подключей:

```console
Reader ...........: Yubico YubiKey FIDO CCID
Application ID ...: D2760001240102010006055532110000
Application type .: OpenPGP
Version ..........: 3.4
Manufacturer .....: Yubico
Serial number ....: 05553211
Name of cardholder: [not set]
Language prefs ...: [not set]
Salutation .......:
URL of public key : [not set]
Login data .......: yk.3i568zcrib5f
Signature PIN ....: not forced
Key attributes ...: rsa4096 rsa4096 rsa4096
Max. PIN lengths .: 127 127 127
PIN retry counter : 3 3 3
Signature counter : 0
KDF setting ......: off
UIF setting ......: Sign=off Decrypt=off Auth=off
Signature key ....: CF5A 305B 808B 7A0F 230D  A064 B3CD 10E5 02E1 9637
      created ....: 2026-08-01 12:00:00
Encryption key....: A5FA A005 5BED 4DC9 889D  38BC 30CB E8C4 B085 B9F7
      created ....: 2026-08-01 12:00:00
Authentication key: 570E 1355 6D01 4C04 8B6D  E2A3 AD9E 24E1 B8CB 9600
      created ....: 2026-08-01 12:00:00
General key info..: sub  rsa4096/0xB3CD10E502E19637 2026-08-01 yk.pn8wgfx67khlhext
sec#  rsa4096/0xF0F2CFEB04341FB5  created: 2026-08-01  expires: never
ssb>  rsa4096/0xB3CD10E502E19637  created: 2026-08-01  expires: 2028-08-01
                                  card-no: 0006 05553211
ssb>  rsa4096/0x30CBE8C4B085B9F7  created: 2026-08-01  expires: 2028-08-01
                                  card-no: 0006 05553211
ssb>  rsa4096/0xAD9E24E1B8CB9600  created: 2026-08-01  expires: 2028-08-01
                                  card-no: 0006 05553211
```

`sec#` означает, что соответствующий ключ недоступен (ключ сертификации хранится без подключения к сети).

Теперь YubiKey готов к использованию!

## Шифрование {#encryption}

Зашифруйте сообщение для самого себя (это удобно для хранения учётных данных или защиты резервных копий):

```bash
printf "test message string\n" |
  gpg --encrypt --armor \
      --recipient $KEY_ID --output encrypted.txt
```

Расшифруйте сообщение — появится запрос PIN пользователя:

```bash
gpg --decrypt --armor encrypted.txt
```

Чтобы зашифровать сообщение для нескольких получателей или ключей, укажите предпочитаемый идентификатор ключа последним:

```bash
printf "test message string\n" |
  gpg --encrypt --armor \
      --recipient $KEY_ID3 \
      --recipient $KEY_ID2 \
      --recipient $KEY_ID1 \
      --output encrypted.txt
```

Чтобы упростить шифрование файлов, используйте [функцию командной оболочки](https://github.com/drduh/config/blob/main/zshrc):

```bash
secret() {
  local file="${1}"
  local output="${file}.$(date +%s).enc"
  gpg --encrypt --armor --recipient "${KEY_ID}" \
      --output "${output}" -- "${file}" &&
    printf '%s -> %s' "${file}" "${output}"
}

reveal() {
  local file="${1}"
  local output="${file%.[0-9]*.enc}"
  gpg --decrypt --output "${output}" -- "${file}" &&
    printf '%s -> %s' "${file}" "${output}"
}
```

Пример вывода:

```console
$ printf 'hello, world!\n' > document.txt

$ secret document.txt
document.txt -> document.txt.1780000000.enc

$ reveal document.txt.*.enc
gpg: encrypted with RSA key, ID 0x0000000000000000
gpg: okay, we are the anonymous recipient.
document.txt.1780000000.enc -> document.txt
```

[drduh/Purse](https://github.com/drduh/Purse) — менеджер секретов на основе GnuPG и YubiKey.

## Подпись {#signature}

Подпишите сообщение:

```bash
printf "test message string\n" |
  gpg --armor --clearsign > signed.txt
```

Проверьте подпись:

```bash
gpg --verify signed.txt
```

Вывод будет примерно таким:

```console
gpg: Signature made Sat 01 Aug 2026 12:00:00 PM UTC
gpg:                using RSA key CF5A305B808B7A0F230DA064B3CD10E502E19637
gpg: Good signature from "yk.pn8wgfx67khlhext" [ultimate]
Primary key fingerprint: 4E2C 1FA3 372C BA96 A06A  C34A F0F2 CFEB 0434 1FB5
     Subkey fingerprint: CF5A 305B 808B 7A0F 230D  A064 B3CD 10E5 02E1 9637
```

## Настройка касания {#configure-touch}

По умолчанию после разблокировки YubiKey с помощью PIN пользователя криптографические операции не требуют от пользователя никаких действий.

Чтобы для каждой операции требовалось касание, задайте политику ключа с помощью [YubiKey Manager](https://developers.yubico.com/yubikey-manager/) и PIN администратора.

Шифрование:

```bash
ykman openpgp keys set-touch dec on
```

> [!NOTE]
> В YubiKey Manager до версии 5.1.0 для шифрования используется `enc` вместо `dec`:

```bash
ykman openpgp keys set-touch enc on
```

> [!NOTE]
> В устаревших версиях YubiKey Manager используется `touch` вместо `set-touch`.

Подпись:

```bash
ykman openpgp keys set-touch sig on
```

Аутентификация:

```bash
ykman openpgp keys set-touch aut on
```

Чтобы просмотреть настроенную политику касания:

```bash
ykman openpgp info
```

Чтобы изменить политику, просмотрите доступные параметры:

```bash
ykman openpgp keys set-touch --help
```

Для работы YubiKey с почтовыми клиентами может подойти `Cached` или `Cached-Fixed`.

Ожидая касания, YubiKey мигает. В Linux для индикации также можно использовать [maximbaz/yubikey-touch-detector](https://github.com/maximbaz/yubikey-touch-detector).

## SSH {#ssh}

Создайте или импортируйте [конфигурацию с усиленной защитой](https://github.com/drduh/YubiKey-Guide/blob/main/config/gpg-agent.conf):

```bash
cd ~/.gnupg
wget https://raw.githubusercontent.com/drduh/YubiKey-Guide/main/config/gpg-agent.conf
```

> [!NOTE]
> Параметры `cache-ttl` **не** действуют, если YubiKey используется как смарт-карта, потому что PIN [кеширует сама смарт-карта](https://dev.gnupg.org/T3362). Чтобы удалить PIN из кеша (аналог `default-cache-ttl` и `max-cache-ttl`), извлеките YubiKey или при редактировании карты задайте `forcesig`, чтобы PIN запрашивался каждый раз.

> [!TIP]
> Чтобы запрос выводился в графическом окне, задайте для `pinentry-program` значение `/usr/bin/pinentry-gnome3`.

**macOS**

Установите pinentry командой `brew install pinentry-mac` или `sudo port install pinentry`, затем отредактируйте `gpg-agent.conf` и укажите в `pinentry-program` путь:

- компьютеры Mac с Apple Silicon: `/opt/homebrew/bin/pinentry-mac`
- компьютеры Mac с Intel: `/usr/local/bin/pinentry-mac` или `/opt/local/bin/pinentry` (MacPorts)
- MacGPG Suite: `/usr/local/MacGPG2/libexec/pinentry-mac.app/Contents/MacOS/pinentry-mac`

Затем выполните `gpgconf --kill gpg-agent`, чтобы изменение вступило в силу.

Для работы графических приложений в macOS [нужна дополнительная настройка](https://jms1.net/yubikey/make-ssh-use-gpg-agent.md).

Создайте файл `$HOME/Library/LaunchAgents/gnupg.gpg-agent.plist` со следующим содержимым:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
    <dict>
        <key>Label</key>
        <string>gnupg.gpg-agent</string>
        <key>RunAtLoad</key>
        <true/>
        <key>KeepAlive</key>
        <false/>
        <key>EnvironmentVariables</key>
        <dict>
            <key>PATH</key>
            <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/usr/local/MacGPG2/bin:/bin</string>
        </dict>
        <key>ProgramArguments</key>
        <array>
            <string>/usr/bin/env</string>
            <string>gpg-connect-agent</string>
            <string>/bye</string>
        </array>
    </dict>
</plist>
```

Загрузите его:

```bash
launchctl load $HOME/Library/LaunchAgents/gnupg.gpg-agent.plist
```

Создайте файл `$HOME/Library/LaunchAgents/gnupg.gpg-agent-symlink.plist` со следующим содержимым:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0/dtd">
<plist version="1.0">
    <dict>
        <key>Label</key>
        <string>gnupg.gpg-agent-symlink</string>
        <key>ProgramArguments</key>
        <array>
            <string>/bin/sh</string>
            <string>-c</string>
            <string>/bin/ln -sf $HOME/.gnupg/S.gpg-agent.ssh $SSH_AUTH_SOCK</string>
        </array>
        <key>RunAtLoad</key>
        <true/>
    </dict>
</plist>
```

Загрузите его:

```bash
launchctl load $HOME/Library/LaunchAgents/gnupg.gpg-agent-symlink.plist
```

Перезагрузите компьютер, чтобы изменения вступили в силу.

**Windows**

В Windows уже могут быть установлены виртуальные считыватели смарт-карт, например считыватель для Windows Hello. Чтобы scdaemon гарантированно использовал именно YubiKey, добавьте его в конфигурацию scdaemon.

Определите имя YubiKey с помощью PowerShell:

```powershell
PS C:\WINDOWS\system32> Get-PnpDevice -Class SoftwareDevice | Where-Object {$_.FriendlyName -like "*YubiKey*"} | Select-Object -ExpandProperty FriendlyName
Yubico YubiKey OTP+FIDO+CCID 0
```

Подробнее см. [How to setup Signed Git Commits with a YubiKey NEO and GPG and Keybase on Windows (2018)](https://www.hanselman.com/blog/HowToSetupSignedGitCommitsWithAYubiKeyNEOAndGPGAndKeybaseOnWindows.aspx).

Отредактируйте `%APPDATA%/gnupg/scdaemon.conf` и добавьте:

```console
reader-port <device name, e.g. Yubico YubiKey OTP+FIDO+CCID 0>
```

Отредактируйте `%APPDATA%/gnupg/gpg-agent.conf` и добавьте:

```console
enable-ssh-support
enable-putty-support
```

Перезапустите агент:

```bash
gpg-connect-agent killagent /bye
gpg-connect-agent /bye
```

Проверьте сведения о YubiKey:

```bash
gpg --card-status
```

Импортируйте открытый ключ и установите абсолютное доверие:

```bash
gpg --import <path to public key file>
```

Получите идентификатор открытого ключа:

```bash
gpg --list-public-keys
```

Экспортируйте открытый ключ SSH:

```bash
gpg --export-ssh-key <public key id>
```

Скопируйте открытый ключ SSH в файл — он соответствует закрытому ключу на YubiKey, и его можно скопировать на хосты, к которым вы подключаетесь по SSH.

Создайте ярлык, указывающий на `gpg-connect-agent /bye`, и поместите его в папку автозагрузки `shell:startup`, чтобы агент запускался после перезагрузки. Измените свойства ярлыка, чтобы он запускался в окне "Minimized" (Свёрнутое в значок).

Теперь PuTTY можно использовать для аутентификации SSH по открытому ключу. Когда сервер запросит проверку открытого ключа, PuTTY перенаправит запрос в GnuPG, который запросит PIN для подтверждения операции.

**WSL**

Цель — настроить SSH-клиент внутри WSL для совместной работы с агентом Windows, например gpg-agent.exe.

Общая схема показана на иллюстрации [архитектуры агентов в WSL](media/schema_gpg.png).

Перенаправление GnuPG для криптографических операций не поддерживается. Подробнее см. [vuori/weasel-pageant](https://github.com/vuori/weasel-pageant).

Один из способов перенаправления — просто `ssh -A` (при этом всё равно нужно выполнить eval для weasel, чтобы настроить локальный ssh-agent); он опирается только на OpenSSH. В этом варианте могут использоваться параметры `ForwardAgent` и `AllowAgentForwarding` в конфигурации ssh/sshd. Однако при перенаправлении сокета ssh не включайте `ForwardAgent` в конфигурации ssh. Подробнее см. раздел [Перенаправление агента SSH](#ssh-agent-forwarding). Для этого нужны Ubuntu 16.04 или новее для WSL и Kleopatra.

Скачайте [vuori/weasel-pageant](https://github.com/vuori/weasel-pageant).

Добавьте `eval $(/mnt/c/<path of extraction>/weasel-pageant -r -a /tmp/S.weasel-pageant)` в rc-файл командной оболочки. Здесь используется именованный сокет, чтобы его можно было указать в директиве `RemoteForward` файла `~/.ssh/config`. Загрузите изменения командой `source ~/.bashrc`.

Выведите ключ SSH командой `ssh-add -l`.

Отредактируйте `~/.ssh/config` и добавьте для каждого хоста, на который перенаправляется агент, следующее:

```console
RemoteForward <remote SSH socket path> /tmp/S.weasel-pageant
```

Путь к удалённому сокету SSH можно узнать командой `gpgconf --list-dirs agent-ssh-socket`

Добавьте в rc-файл командной оболочки следующее:

```bash
export SSH_AUTH_SOCK=$(gpgconf --list-dirs agent-ssh-socket)
```

Добавьте в `/etc/ssh/sshd_config` следующее:

```console
StreamLocalBindUnlink yes
```

Перезагрузите конфигурацию демона SSH:

```bash
sudo service sshd reload
```

Извлеките YubiKey и перезагрузите компьютер. Снова войдите в Windows, откройте консоль WSL и введите `ssh-add -l` — вывода быть не должно.

Подключите YubiKey и введите ту же команду, чтобы вывести ключ SSH.

Подключитесь к удалённому хосту и с помощью `ssh-add -l` убедитесь, что перенаправление работает.

Перенаправление агента можно выстроить в цепочку через несколько хостов. Настройте каждый хост по той же [схеме](#chained-forwarding).

Другой способ — библиотека [usbipd-win](https://github.com/dorssel/usbipd-win). Если после настройки usbipd-win возникают проблемы с доступом к YubiKey в WSL, возможно, потребуется добавить собственные правила polkit, чтобы у службы pcscd были нужные права. Вот пример конфигурации с группой scard (логика с группой необязательна):

Создайте новый файл правил /etc/polkit-1/rules.d/99-pcscd.rules:

```conf
polkit.addRule(function(action, subject) {
    if (action.id == "org.debian.pcsc-lite.access_card" &&
        subject.isInGroup("scard")) {
        return polkit.Result.YES;
    }
});

polkit.addRule(function(action, subject) {
    if (action.id == "org.debian.pcsc-lite.access_pcsc" &&
        subject.isInGroup("scard")) {
        return polkit.Result.YES;
    }
});
```

### Замена агентов {#replace-agents}

Чтобы запустить `gpg-agent` для SSH, используйте команду `gpg-connect-agent /bye` или `gpgconf --launch gpg-agent`.

Добавьте в rc-файл командной оболочки следующее:

```bash
export GPG_TTY=$(tty)
export SSH_AUTH_SOCK=$(gpgconf --list-dirs agent-ssh-socket)
gpgconf --launch gpg-agent
gpg-connect-agent updatestartuptty /bye > /dev/null
```

Для fish файл `config.fish` должен выглядеть так (эти строки стоит поместить в блок `is-interactive`):

```fish
set -x GPG_TTY (tty)
set -x SSH_AUTH_SOCK (gpgconf --list-dirs agent-ssh-socket)
gpgconf --launch gpg-agent
```

Если для перенаправления ssh-agent используется `ForwardAgent`, `SSH_AUTH_SOCK` нужно задать только на *локальном* хосте, к которому подключён YubiKey. На *удалённом* хосте `ssh` при подключении сам задаёт `SSH_AUTH_SOCK` значение вида `/tmp/ssh-mXzCzYT2Np/agent.7541`. **Не** задавайте `SSH_AUTH_SOCK` на удалённом хосте — это нарушит [перенаправление агента SSH](#ssh-agent-forwarding).

Для `S.gpg-agent.ssh` (подробнее см. [Перенаправление агента SSH](#ssh-agent-forwarding)) `SSH_AUTH_SOCK` нужно задать и на *удалённом* хосте. Однако `GPG_TTY` на *удалённом* хосте задавать не следует; объяснение приведено в том разделе.

### Копирование открытого ключа {#copy-public-key}

> [!TIP]
> Импортировать открытый ключ GnuPG, чтобы использовать только SSH, **не** нужно.

Скопируйте вывод `ssh-add` и вставьте его в файл `authorized_keys` на сервере:

```console
$ ssh-add -L
ssh-rsa AAAAB4NzaC1yc2EAAAADAQABAAACAz[...]zreOKM+HwpkHzcy9DQcVG2Nw== cardno:000605553211
```

**Необязательно.** Сохраните открытый ключ для настройки файла идентификации (identity file). По умолчанию SSH пытается использовать все идентификационные данные, доступные через агент. Часто разумно точно задать, какие ключи SSH будет использовать для подключения к серверу, например чтобы разделить разные роли или [чтобы ненадёжные SSH-серверы не могли вас идентифицировать по отпечатку](https://words.filippo.io/ssh-whoami-filippo-io/). Для этого используйте аргумент командной строки `-i [identity_file]` или параметры `IdentityFile` и `IdentitiesOnly` в `.ssh/config`.

Аргумент `IdentityFile` традиционно содержит путь к файлу *закрытого* ключа (например, `IdentityFile ~/.ssh/id_rsa`). Для YubiKey `IdentityFile` должен указывать на файл *открытого* ключа, а `ssh` выберет подходящий закрытый ключ из доступных через ssh-agent. Чтобы `ssh` не перебирал все ключи в агенте, используйте `IdentitiesOnly yes` вместе с одним или несколькими параметрами `-i` или `IdentityFile` для целевого хоста.

Повторим: с `IdentitiesOnly yes` `ssh` не перебирает открытые ключи, загруженные в `ssh-agent` или `gpg-agent`. Это значит, что аутентификация по открытому ключу не будет выполняться, если ключ явно не указан в `ssh -i [identity_file]` или в `.ssh/config` для конкретного хоста.

Если используется YubiKey, извлеките открытый ключ из агента ssh:

```bash
ssh-add -L | grep "cardno:000605553211" > ~/.ssh/id_rsa_yubikey.pub
```

Затем явно свяжите этот хранящийся на YubiKey ключ с хостом, например `github.com`, следующим образом:

```console
$ cat << EOF >> ~/.ssh/config
Host github.com
  IdentitiesOnly yes
  IdentityFile ~/.ssh/id_rsa_yubikey.pub
EOF
```

Подключитесь с аутентификацией по открытому ключу:

```console
$ ssh git@github.com -vvv
[...]
debug2: key: cardno:000605553211 (0x1234567890),
debug1: Authentications that can continue: publickey
debug3: start over, passed a different list publickey
debug3: preferred gssapi-keyex,gssapi-with-mic,publickey,keyboard-interactive,password
debug3: authmethod_lookup publickey
debug3: remaining preferred: keyboard-interactive,password
debug3: authmethod_is_enabled publickey
debug1: Next authentication method: publickey
debug1: Offering RSA public key: cardno:000605553211
debug3: send_pubkey_test
debug2: we sent a publickey packet, wait for reply
debug1: Server accepts key: pkalg ssh-rsa blen 535
debug2: input_userauth_pk_ok: fp e5:de:a5:74:b1:3e:96:9b:85:46:e7:28:53:b4:82:c3
debug3: sign_and_send_pubkey: RSA e5:de:a5:74:b1:3e:96:9b:85:46:e7:28:53:b4:82:c3
debug1: Authentication succeeded (publickey).
[...]
```

> [!TIP]
> Чтобы использовать несколько SSH-подключений, используйте параметр [ControlMaster](https://en.wikibooks.org/wiki/OpenSSH/Cookbook/Multiplexing).

### Импорт ключей SSH {#import-ssh-keys}

Если у вас есть ключи SSH, которые нужно сделать доступными через `gpg-agent`, их необходимо импортировать. После этого удалите исходные закрытые ключи. При импорте ключа `gpg-agent` использует имя файла ключа в качестве метки — так проще отследить, откуда взялся ключ. В этом примере изначально подключён только YubiKey, и импортируется `~/.ssh/id_rsa`:

```console
$ ssh-add -l
4096 SHA256:... cardno:00060123456 (RSA)

$ ssh-add ~/.ssh/id_rsa && rm ~/.ssh/id_rsa
```

При вызове `ssh-add` появится запрос парольной фразы ключа SSH, затем программа `pinentry` попросит ввести и подтвердить новую парольную фразу для шифрования преобразованного ключа в хранилище ключей GnuPG.

Перенесённый ключ появится в выводе `ssh-add -l`:

```console
$ ssh-add -l
4096 SHA256:... cardno:00060123456 (RSA)
2048 SHA256:... /Users/username/.ssh/id_rsa (RSA)
```

Чтобы вывести отпечатки MD5, которые используют команды `KEYINFO` и `DELETE_KEY` в `gpg-connect-agent`:

```console
$ ssh-add -E md5 -l
4096 MD5:... cardno:00060123456 (RSA)
2048 MD5:... /Users/username/.ssh/id_rsa (RSA)
```

При использовании ключа будет вызвана программа `pinentry` для запроса парольной фразы ключа. Парольная фраза кешируется на время бездействия между использованиями до 10 минут, но не более чем на 2 часа.

### Перенаправление агента SSH {#ssh-agent-forwarding}

> [!CAUTION]
> Перенаправление агента SSH может [создавать дополнительный риск](https://matrix.org/blog/2019/05/08/post-mortem-and-remediations-for-apr-11-security-incident/#ssh-agent-forwarding-should-be-disabled) — действуйте с осторожностью!

Есть два способа перенаправления ssh-agent: один предоставляет OpenSSH, другой — GnuPG.

Второй способ может быть менее безопасным, так как перенаправляется просто необработанный сокет (в отличие от `S.gpg-agent.extra` с ограниченной функциональностью; если реализованный в OpenSSH `ForwardAgent` тоже просто перенаправляет необработанный сокет, то оба способа одинаково небезопасны). Но у второго способа есть удобство: агент можно перенаправить один раз и использовать везде на удалённом хосте. Поэтому ещё раз: действуйте с осторожностью!

Например, в tmux при подключении к удалённым хостам и присоединении к существующему сеансу нет переменных окружения, таких как `$SSH_AUTH_SOCK`. Для каждой командной оболочки приходится находить сокет и выполнять `export SSH_AUTH_SOCK=/tmp/ssh-agent-xxx/xxxx.socket`. Если же `S.gpg-agent.ssh` находится в фиксированном месте, его можно указать как ssh-agent в rc-файлах командной оболочки.

#### Использование ssh-agent {#use-ssh-agent}

Выполните `ssh -A remote` на локальном хосте. На удалённом хосте `ssh-add -l` должен вывести открытый ключ с YubiKey как запись `cardno:`. Включайте `ForwardAgent yes` только для хостов, которым вы явно доверяете!

#### Использование S.gpg-agent.ssh {#use-sgpg-agentssh}

Прежде чем продолжить, настройте [перенаправление агента GnuPG](#gnupg-agent-forwarding) и запишите локальное и удалённое значения `gpgconf --list-dirs agent-ssh-socket`.

Отредактируйте `~/.ssh/config` и добавьте удалённый хост:

```console
Host
  Hostname remote-host.tld
  StreamLocalBindUnlink yes
  RemoteForward /run/user/1000/gnupg/S.gpg-agent.ssh /run/user/1000/gnupg/S.gpg-agent.ssh
  #RemoteForward [remote socket] [local socket]
  #Note that ForwardAgent is not wanted here!
```

После успешного подключения к удалённому хосту убедитесь, что `/run/user/1000/gnupg/S.gpg-agent.ssh` существует, и задайте `SSH_AUTH_SOCK`:

```bash
export SSH_AUTH_SOCK="/run/user/$UID/gnupg/S.gpg-agent.ssh"
```

Теперь `ssh-add -l` должен вывести правильный открытый ключ.

В этом процессе gpg-agent на удалённом хосте не участвует, поэтому `gpg-agent.conf` на удалённом хосте ни на что не влияет. Кроме того, pinentry вызывается локально.

#### Цепочка перенаправления {#chained-forwarding}

Чтобы с помощью `ssh-agent` из OpenSSH перенаправить агент на *третий* хост, выполните `ssh -A third` на *удалённом* хосте.

Если вы используете `S.gpg-agent.ssh` и уже выполнили шаги выше, так что на *удалённом* хосте есть `S.gpg-agent.ssh`, и хотите перенаправить этот агент на *третий* хост, сначала настройте `sshd_config` и `SSH_AUTH_SOCK` на *третьем* хосте так же, как на *удалённом*, затем настройте SSH на *удалённом* хосте, добавив следующие строки:

```console
Host third
  Hostname third-host.tld
  StreamLocalBindUnlink yes
  RemoteForward /run/user/1000/gnupg/S.gpg-agent.ssh /run/user/1000/gnupg/S.gpg-agent.ssh
  #RemoteForward [remote socket] [local socket]
  #Note that ForwardAgent is not wanted here!
```

Путь нужно задать в соответствии со значением `gpgconf --list-dirs agent-ssh-socket` на *удалённом* и *третьем* хостах.

## GitHub {#github}

Используйте YubiKey, чтобы подписывать коммиты и теги Git и проходить аутентификацию в GitHub по SSH. Для аутентификации сначала добавьте экспортированный открытый ключ SSH в [GitHub SSH keys](https://github.com/settings/keys) (ключи SSH в GitHub).

Настройте ключ подписи:

```bash
git config --global user.signingkey $KEY_ID
```

Либо, если для подписи вы используете упомянутый выше `IdentityFile` (ключ SSH):

```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_rsa_yubikey.pub
```

Задайте параметры `user.name` и `user.email` в соответствии с идентификатором пользователя, связанным с этим ключом:

```bash
git config --global user.name 'YubiKey User'
git config --global user.email yubikey@example.com
```

Чтобы подписать коммиты или теги, используйте параметр `-S` или включите подпись коммитов и тегов по умолчанию:

```bash
git config --global commit.gpgsign true
git config --global tag.gpgSign true
```

**Windows**

Настройте аутентификацию:

```bash
git config --global core.sshcommand "plink -agent"
git config --global gpg.program 'C:\Program Files (x86)\GnuPG\bin\gpg.exe'
```

Затем измените URL репозитория на `git@github.com:USERNAME/repository`

## Перенаправление агента GnuPG {#gnupg-agent-forwarding}

YubiKey может подписывать коммиты Git и расшифровывать файлы на удалённых хостах благодаря перенаправлению операций агента GnuPG. Чтобы подключиться через промежуточный хост и затем обращаться к GitHub по SSH, см. раздел [Перенаправление агента SSH](#ssh-agent-forwarding).

После настройки перенаправления GnuPG на удалённом хосте обращается через перенаправленный сокет к gpg-agent на локальном хосте. Локальный агент обращается к YubiKey и выводит все запросы PIN локально.

На удалённом хосте отредактируйте `/etc/ssh/sshd_config` и задайте `StreamLocalBindUnlink yes`.

**Необязательно.** Если на удалённом хосте нет прав root для редактирования `/etc/ssh/sshd_config`, то, чтобы перенаправление заработало, нужно удалить сокет, расположенный по пути `gpgconf --list-dir agent-socket` на удалённом хосте. Подробнее см. [вики-страницу GnuPG AgentForwarding](https://wiki.gnupg.org/AgentForwarding).

Импортируйте открытый ключ на удалённом хосте. На локальном хосте скопируйте связку открытых ключей на удалённый хост:

```bash
scp ~/.gnupg/pubring.kbx remote:~/.gnupg/
```

В современных дистрибутивах, например Fedora 30, задавать `RemoteForward` в `~/.ssh/config` не нужно

### Устаревшие дистрибутивы {#legacy-distributions}

На локальном хосте выполните:

```bash
gpgconf --list-dirs agent-extra-socket
```

Команда должна вернуть путь к agent-extra-socket — `/run/user/1000/gnupg/S.gpg-agent.extra`, хотя в некоторых дистрибутивах Linux (и в macOS) это может быть `/home/<user>/.gnupg/S.gpg-agent.extra`

Найдите сокет агента на **удалённом** хосте:

```bash
gpgconf --list-dirs agent-socket
```

Команда должна вернуть путь вида `/run/user/1000/gnupg/S.gpg-agent`

Наконец, включите перенаправление агента для нужного хоста, добавив в `~/.ssh/config` на локальном хосте следующее (пути к сокетам агента могут отличаться):

```console
Host
  Hostname remote-host.tld
  StreamLocalBindUnlink yes
  RemoteForward /run/user/1000/gnupg/S.gpg-agent /run/user/1000/gnupg/S.gpg-agent.extra
  #RemoteForward [remote socket] [local socket]
```

Возможно, потребуется отредактировать `gpg-agent.conf` на *локальном* хосте и добавить следующее:

```console
pinentry-program /usr/bin/pinentry-gtk-2
extra-socket /run/user/1000/gnupg/S.gpg-agent.extra
```

> [!IMPORTANT]
> Программа pinentry запускается на *локальном* хосте, а не на удалённом.

Можно использовать любую программу pinentry, кроме `pinentry-tty` и `pinentry-curses`. Причина в том, что локальный `gpg-agent` может быть запущен без терминала (systemd без локально заданной `$GPG_TTY`, указывающей, на каком tty он работает) и поэтому не сможет получить PIN. Сообщения об ошибках на удалённом хосте могут вводить в заблуждение, говоря об *IO Error* (ошибке ввода-вывода). (Да, внутри действительно происходит *IO Error*, поскольку она возникает при записи в tty или чтении из него, когда подходящего tty нет, но для конечного пользователя такое сообщение неудобно.)

Подробнее, в том числе об устранении неполадок, см. [Issue 85](https://github.com/drduh/YubiKey-Guide/issues/85).

### Цепочка перенаправления агента GnuPG {#chained-gnupg-agent-forwarding}

Предположим, вы выполнили шаги выше, на *удалённом* хосте есть `S.gpg-agent`, и вы хотите перенаправить этот агент на *третий* хост. Сначала, возможно, потребуется настроить `sshd_config` на *третьем* хосте так же, как на *удалённом*, затем добавьте в конфигурацию ssh на *удалённом* хосте следующие строки:

```console
Host third
  Hostname third-host.tld
  StreamLocalBindUnlink yes
  RemoteForward /run/user/1000/gnupg/S.gpg-agent /run/user/1000/gnupg/S.gpg-agent
  #RemoteForward [remote socket] [local socket]
```

Измените путь в соответствии со значением `gpgconf --list-dirs agent-socket` на *удалённом* и *третьем* хостах.

На *локальном* хосте есть `S.gpg-agent.extra`, а на *удалённом* и *третьем* — только `S.gpg-agent`.

## Использование нескольких YubiKey {#using-multiple-yubikeys}

Когда ключ GnuPG добавляется на YubiKey командой `keytocard`, он удаляется из связки ключей, а вместо него добавляется локальная ссылка на карту («заглушка ключа», stub). Заглушка содержит идентификатор ключа GnuPG и серийный номер YubiKey.

Когда подключ добавляется на ещё один YubiKey, заглушка перезаписывается и теперь указывает на последний YubiKey. GnuPG будет запрашивать конкретный YubiKey по серийному номеру, указанному в заглушке, и не распознает другой YubiKey с другим серийным номером.

> [!TIP]
> Чтобы записать ключи из резервной копии на новый набор YubiKey, например после того как YubiKey был повреждён или утерян, используйте [утилиту восстановления](recovery/README.md).

Чтобы считать дополнительный YubiKey и заново создать правильную заглушку:

```bash
gpg-connect-agent "scd serialno" "learn --force" /bye
```

Либо удалите с помощью скрипта теневой ключ GnuPG, в котором хранится серийный номер (см. [GnuPG #T2291](https://dev.gnupg.org/T2291)):

```bash
mkdir -p ~/scripts && cat >> ~/scripts/remove-keygrips.sh <<EOF
#!/usr/bin/env bash
(( $# )) || { echo "Specify a key." >&2; exit 1; }
KEYGRIPS=$(gpg --with-keygrip --list-secret-keys "$@" | awk '/Keygrip/ { print $3 }')
for keygrip in $KEYGRIPS
do
  rm "$HOME/.gnupg/private-keys-v1.d/$keygrip.key" 2> /dev/null
done

gpg --card-status
EOF

chmod +x ~/scripts/remove-keygrips.sh

~/scripts/remove-keygrips.sh $KEY_ID
```

Подробнее см. обсуждение в [#19](https://github.com/drduh/YubiKey-Guide/issues/19) и [#112](https://github.com/drduh/YubiKey-Guide/issues/112).

## Электронная почта {#email}

YubiKey может расшифровывать и подписывать письма и вложения в [Thunderbird](https://www.thunderbird.net/) и [Mutt](http://www.mutt.org/). Thunderbird поддерживает OAuth и может работать с Gmail. Подробнее см. [это руководство EFF](https://ssd.eff.org/en/module/how-use-pgp-linux).

### Thunderbird {#thunderbird}

Чтобы настроить YubiKey в Thunderbird с внешним поставщиком GPG, следуйте [инструкциям Mozilla](https://wiki.mozilla.org/Thunderbird:OpenPGP:Smartcards#Configure_an_email_account_to_use_an_external_GnuPG_key).

> [!NOTE]
> Thunderbird [не сможет расшифровать](https://github.com/drduh/YubiKey-Guide/issues/448) сообщения, если задан параметр `armor`.
> Если вы видите ошибку `gpg: [don't know]: invalid packet (ctb=2d)` или `message cannot be decrypted (there are unknown problems with this encrypted message)`, удалите `armor` из `~/.gnupg/gpg.conf`, затем перезапустите Thunderbird.

### Mailvelope {#mailvelope}

[Mailvelope](https://www.mailvelope.com/en) позволяет использовать YubiKey с Gmail и другими почтовыми сервисами.

> [!NOTE]
> Mailvelope [не работает](https://github.com/drduh/YubiKey-Guide/issues/178), если в `gpg.conf` задан параметр `throw-keyids`

В macOS установите gpgme с помощью Homebrew:

```bash
brew install gpgme
```

Чтобы разрешить Chrome запускать gpgme, отредактируйте `~/Library/Application\ Support/Google/Chrome/NativeMessagingHosts/gpgmejson.json` и добавьте:

```json
{
    "name": "gpgmejson",
    "description": "Integration with GnuPG",
    "path": "/usr/local/bin/gpgme-json",
    "type": "stdio",
    "allowed_origins": [
        "chrome-extension://kajibbejlbohfaggdiogboambcijhkke/"
    ]
}
```

Измените путь по умолчанию, чтобы Chrome мог найти GnuPG:

```bash
sudo launchctl config user path /usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
```

Наконец, установите [расширение Mailvelope](https://chromewebstore.google.com/detail/mailvelope/kajibbejlbohfaggdiogboambcijhkke) из интернет-магазина Chrome.

### Mutt {#mutt}

Mutt можно использовать из командной строки или через его текстовый интерфейс. Чтобы включить поддержку OpenPGP, скопируйте пример файла конфигурации GnuPG (`/usr/share/doc/mutt/samples/gpg.rc`), задайте в этом файле `pgp_default_key`, `pgp_sign_as` и `pgp_autosign`, затем добавьте в файл конфигурации Mutt (`~/.muttrc`) строку source, подключающую его.

> [!NOTE]
> `pinentry-tty`, заданная в `gpg-agent.conf` как программа pinentry, может вызывать проблемы с Mutt, потому что Mutt использует curses; вместо неё используйте `pinentry-curses` или другую графическую программу pinentry.

## Сервер ключей {#keyserver}

При желании опубликуйте открытый ключ на сервере ключей, чтобы другие могли его найти.

Выберите сервер, на который его отправить:

```bash
gpg --send-key $KEY_ID
gpg --keyserver keys.gnupg.net --send-key $KEY_ID
gpg --keyserver hkps://keyserver.ubuntu.com:443 --send-key $KEY_ID
```

Или, если вы загружаете ключ на [keys.openpgp.org](https://keys.openpgp.org/about/usage):

```bash
gpg --export $KEY_ID | curl -T - https://keys.openpgp.org
```

URL для получения ключа также можно сохранить на YubiKey:

```bash
URL="hkps://keyserver.ubuntu.com:443/pks/lookup?op=get&search=${KEY_ID}"
```

Отредактируйте YubiKey командой `gpg --edit-card`, используя PIN администратора:

```console
gpg/card> admin

gpg/card> url
URL to retrieve public key: hkps://keyserver.ubuntu.com:443/pks/lookup?op=get&search=0xFF00000000000000

gpg/card> quit
```

# Обновление ключей {#updating-keys}

OpenPGP не обеспечивает [совершенную прямую секретность](https://en.wikipedia.org/wiki/Forward_secrecy) (forward secrecy): если ключ шифрования скомпрометирован, злоумышленник может расшифровать сообщения, зашифрованные для этого ключа в прошлом. Продление или замена подключей ограничивает дальнейшее использование утерянного или устаревшего ключа, но может не защитить ранее зашифрованные данные. Выберите срок действия и график продления в соответствии с допустимым для вас уровнем риска и тем, сколько усилий вы готовы тратить на обслуживание.

Когда срок действия подключа истекает, его можно либо продлить, либо заменить. Оба действия требуют доступа к ключу сертификации.

- Продление сохраняет существующие подключи и, как правило, проще в исполнении.
- Замена создаёт новые подключи и может быть безопаснее: новые подключи **не** смогут расшифровывать прежние данные, проходить аутентификацию SSH и т. д. Открытый ключ потребуется обновить, а все зашифрованные данные необходимо расшифровать и заново зашифровать для новых подключей. По сути этот процесс равнозначен настройке нового YubiKey.

Ни один из способов обновления не лучше другого: какой из них использовать и стоит ли вообще задавать подключам срок действия, решается исходя из ваших взглядов на управление идентификационными данными и вашей модели угроз. В идеале подключи были бы одноразовыми: использовались бы только один раз для каждой отдельной операции шифрования, подписи и аутентификации, однако на практике с YubiKey это не слишком реально и не оправдано. Опытные пользователи могут выделить изолированный от сети компьютер для частой замены учётных данных.

Чтобы продлить или заменить подключи, выполните те же шаги, что и при создании ключей: загрузите защищённую среду, установите необходимое программное обеспечение и отключите сеть.

Подключите переносной накопитель с ключом сертификации и определите путь к устройству.

Расшифруйте и смонтируйте зашифрованный том:

```bash
sudo cryptsetup luksOpen /dev/sdc1 gnupg-secrets
sudo mkdir -p /mnt/encrypted-storage
sudo mount /dev/mapper/gnupg-secrets /mnt/encrypted-storage
```

Смонтируйте незашифрованный открытый раздел:

```bash
sudo mkdir -p /mnt/public
sudo mount /dev/sdc2 /mnt/public
```

Скопируйте исходный материал закрытых ключей во временный рабочий каталог (предварительно исправив в команде имя каталога на зашифрованном носителе):

```bash
export GNUPGHOME=$(mktemp -d "${TMPDIR:-/tmp}/$(date +%Y.%m.%d)-XXXXXXXX")
cp -avi /mnt/encrypted-storage/2026.*/* $GNUPGHOME/
```

Убедитесь, что идентификатор пользователя доступен, и задайте значения идентификатора и отпечатка ключа:

```bash
gpg -K
export KEY_FP=$(gpg -k --with-colons $IDENTITY | awk -F: '/^fpr:/ { print $10; exit }')
export KEY_ID="${KEY_FP: -16}"
printf "\nKey ID/Fingerprint:\t%s\n%s\n\n" "$KEY_ID" "$KEY_FP"
```

Вспомните парольную фразу ключа сертификации и задайте её, например:

```bash
export CERTIFY_PASS="ABCD-0123-IJKL-4567-QRST-UVWX"
```

## Продление подключей {#renew-subkeys}

Задайте новую дату окончания срока действия:

```bash
export KEY_EXPIRATION=2028-09-01
```

Продлите подключи:

```bash
printf '%s' "$CERTIFY_PASS" |
  gpg --batch --pinentry-mode loopback \
      --passphrase-fd 0 --quick-set-expire "$KEY_FP" "$KEY_EXPIRATION" \
    $(gpg -K --with-colons |
      awk -F: '$1=="fpr" { if (++n > 1) printf "%s ", $10 } END { print "" }')
```

Экспортируйте обновлённый открытый ключ:

```bash
gpg --armor --export $KEY_ID |
  sudo tee /mnt/public/public-$KEY_ID-$(date +%F).asc
```

Перенесите открытый ключ на целевой хост и импортируйте его:

```bash
gpg --import /mnt/public/*.asc
```

Либо опубликуйте его на общедоступном сервере ключей и скачайте оттуда:

```bash
gpg --send-key $KEY_ID
gpg --recv $KEY_ID
```

Срок действия идентификатора GnuPG будет продлён, и операции шифрования и подписи снова станут доступны.

Обновлять открытый ключ SSH на удалённых хостах **не** нужно.

## Замена подключей {#rotate-subkeys}

Выполните исходную процедуру из раздела [Создание подключей](#create-subkeys).

Прежние подключи можно удалить из идентификатора пользователя.

В завершение перенесите новые подключи на YubiKey.

Скопируйте **новый** временный рабочий каталог на зашифрованный носитель, который всё ещё смонтирован:

```bash
sudo cp -avi $GNUPGHOME /mnt/encrypted-storage
```

Отмонтируйте и закройте зашифрованный том:

```bash
sudo umount /mnt/encrypted-storage
sudo cryptsetup luksClose gnupg-secrets
```

Экспортируйте обновлённый открытый ключ:

```bash
sudo mkdir -p /mnt/public
sudo mount /dev/sdc2 /mnt/public
gpg --armor --export $KEY_ID | sudo tee /mnt/public/$KEY_ID-$(date +%F).asc
sudo umount /mnt/public
```

Извлеките накопитель и, следуя исходным шагам, перенесите новые подключи (`4`, `5` и `6`) на YubiKey, заменив существующие.

Перезагрузите компьютер или безопасно сотрите временный рабочий каталог.

# Сброс YubiKey {#reset-yubikey}

Если число попыток ввода PIN исчерпано, YubiKey блокируется; его необходимо [сбросить](https://developers.yubico.com/ykneo-openpgp/ResetApplet.html) и заново настроить с помощью зашифрованной резервной копии.

Сбросьте приложение OpenPGP с помощью YubiKey Manager:

```bash
ykman openpgp reset
```

YubiKey также можно сбросить скриптом [scripts/resetCard.sh](https://github.com/drduh/YubiKey-Guide/blob/main/scripts/resetCard.sh) или скопировав следующие команды в файл и выполнив `gpg-connect-agent -r <filename>`:

```console
/hex
scd serialno
scd apdu 00 20 00 81 08 40 40 40 40 40 40 40 40
scd apdu 00 20 00 81 08 40 40 40 40 40 40 40 40
scd apdu 00 20 00 81 08 40 40 40 40 40 40 40 40
scd apdu 00 20 00 81 08 40 40 40 40 40 40 40 40
scd apdu 00 20 00 83 08 40 40 40 40 40 40 40 40
scd apdu 00 20 00 83 08 40 40 40 40 40 40 40 40
scd apdu 00 20 00 83 08 40 40 40 40 40 40 40 40
scd apdu 00 20 00 83 08 40 40 40 40 40 40 40 40
scd apdu 00 e6 00 00
scd apdu 00 44 00 00
/echo Card has been successfully reset.
/bye
```

Чтобы завершить сброс, извлеките и снова вставьте YubiKey.

# Дополнительное усиление защиты {#optional-hardening}

Следующие шаги могут повысить безопасность и конфиденциальность YubiKey.

## Повышение энтропии {#improving-entropy}

Для создания криптографических ключей нужна высококачественная [случайность](https://www.random.org/randomness/), которая измеряется энтропией. Большинство операционных систем используют программные генераторы псевдослучайных чисел или аппаратные генераторы случайных чисел (HRNG) процессора.

При желании можно использовать устройство вроде [OneRNG](https://onerng.info/onerng/), чтобы [увеличить скорость](https://lwn.net/Articles/648550/) поступления энтропии и, возможно, её качество.

Перед созданием ключей настройте [rng-tools](https://wiki.archlinux.org/title/Rng-tools):

```bash
sudo apt -y install at rng-tools python3-gnupg openssl
wget https://github.com/OneRNG/onerng.github.io/raw/master/sw/onerng_3.7-1_all.deb
```

Проверьте пакет:

```bash
sha256sum onerng_3.7-1_all.deb
```

Значение должно совпадать с этим:

```console
b7cda2fe07dce219a95dfeabeb5ee0f662f64ba1474f6b9dddacc3e8734d8f57
```

Установите пакет:

```bash
sudo dpkg -i onerng_3.7-1_all.deb
echo "HRNGDEVICE=/dev/ttyACM0" | sudo tee /etc/default/rng-tools
```

Подключите устройство и перезапустите rng-tools:

```bash
sudo atd
sudo service rng-tools restart
```

## Включение KDF {#enable-kdf}

> [!IMPORTANT]
> Эта функция несовместима с устаревшими версиями GnuPG, особенно с мобильными клиентами, и может приводить к тому, что PIN всегда отклоняется.

Этот шаг необходимо выполнить до смены PIN или переноса ключей, иначе возникнет ошибка: `gpg: error for setup KDF: Conditions of use not satisfied`

Функция формирования ключа (KDF) вычисляет из PIN производное значение перед отправкой на карту, что снижает риск раскрытия PIN.

Включите KDF, используя PIN администратора по умолчанию `12345678`:

```bash
gpg --command-fd 0 --pinentry-mode loopback --card-edit <<EOF
admin
kdf-setup
12345678
EOF
```

## Сетевые аспекты {#network-considerations}

Этот раздел в первую очередь касается систем Debian/Ubuntu, но описанные принципы применимы к любой подключённой к сети системе.

Используете ли вы виртуальную машину, установку на отдельное оборудование или временно запущенную live-систему, начинайте работу *без* подключения к сети и перед подключением отключите все ненужные службы, прослушивающие все интерфейсы.

Такие службы, как cups и avahi, по умолчанию могут прослушивать сеть, увеличивая потенциальную поверхность атаки. В этом процессе считайте любую сеть ненадёжной.

**Отключение прослушивающих служб**

Убедитесь, что запущены только необходимые сетевые службы. Если службы не существует, может появиться предупреждение "Failed to stop":

```bash
sudo systemctl stop bluetooth exim4 cups avahi avahi-daemon sshd
```

**Межсетевой экран**

Включите базовую политику межсетевого экрана: *запрещать входящие, разрешать исходящие*.

В Debian политика межсетевого экрана узла по умолчанию не включена.

В Ubuntu встроен [ufw](https://ubuntu.com/server/docs/how-to/security/firewalls/), который можно включить командой:

```bash
sudo ufw enable
```

В системах без `ufw` на смену `iptables` приходит `nftables`. В [вики nftables есть примеры](https://wiki.nftables.org/wiki-nftables/index.php/Simple_ruleset_for_a_workstation) базовой политики *запрещать входящие, разрешать исходящие*. Политика `fw.inet.basic` охватывает и IPv4, и IPv6.

При создании загрузочного носителя скачайте этот README и другие материалы на другой внешний накопитель, чтобы эта информация была доступна без подключения к сети.

Какую бы политику вы ни настроили, запишите её содержимое в файл (например, `nftables.conf`) и примените политику следующей командой:

```bash
sudo nft -f ./nftables.conf
```

**Проверка состояния системы**

`NetworkManager` должен быть единственной службой, прослушивающей порт 68/udp для получения аренды DHCP (и 58/icmp6, если у вас есть IPv6).

Чтобы просмотреть аргументы командной строки процесса, выведите дерево процессов командой `ps axjf`.

```bash
# Dump network state information
sudo ss -anp -A inet

# List all processes in a process tree
ps axjf

# BSD syntax, list all processes but no process tree
ps aux
```

Если вы обнаружите другие ненужные процессы, прослушивающие сеть, запишите их и отключите одним из следующих способов:

```bash
# Stop services managed by systemctl
sudo systemctl stop <process-name>

# Terminate the process by matching its command line string
sudo pkill -f '<process-name-or-command-line-string>'

# Obtain the process ID
pgrep -f '<process-name-or-command-line-string>'

# Terminate the process ID
sudo kill <pid>
```

Проверив состояние системы и применив нужные ограничения, подключайтесь к сети только для того, чтобы скачать необходимое программное обеспечение. Перед созданием ключей снова отключитесь от сети.

# Примечания {#notes}

1. У YubiKey есть две конфигурации USB, которые выбираются коротким или долгим касанием. По умолчанию короткое касание может ввести строку одноразового пароля, начинающуюся с `cccccccc`. Это поведение не связано с работой OpenPGP; его можно перенести во вторую конфигурацию или отключить командой `ykman config usb -d OTP`.

1. Использование YubiKey для GnuPG не мешает использовать [другие функции](https://developers.yubico.com/), например [WebAuthn](https://developers.yubico.com/WebAuthn/) и [OTP](https://developers.yubico.com/OTP/).

1. Чтобы переключиться между YubiKey, извлеките первый YubiKey и перезапустите gpg-agent, ssh-agent и pinentry командой `pkill "gpg-agent|ssh-agent|pinentry" ; eval $(gpg-agent --daemon --enable-ssh-support)`, затем вставьте другой YubiKey и выполните `gpg-connect-agent updatestartuptty /bye`.

1. Чтобы использовать YubiKey на нескольких компьютерах, импортируйте соответствующие открытые ключи, затем убедитесь, что YubiKey виден в выводе `gpg --card-status`. Установите для импортированных открытых ключей абсолютное доверие с помощью `trust` и `5`, после чего `gpg --list-secret-keys` покажет правильный доверенный ключ.

1. Чтобы [участвовать в вечеринках подписания ключей](https://www.gnupg.org/gph/en/manual/x334.html) (keysigning parties), для подписи [импортированных открытых ключей](https://gist.github.com/F21/b0e8c62c49dfab267ff1d0c6af39ab84) сначала нужно подготовить защищённую временную среду и импортировать в эту изолированную среду ключ сертификации; подключом подписи нельзя подписывать [импортированные открытые ключи](https://security.stackexchange.com/questions/153057/possible-to-sign-an-imported-key-with-a-subkey-using-gpg).

# Устранение неполадок {#troubleshooting}

Симптом или ошибка | Рекомендуемое действие
--- | ---
Общие вопросы о GnuPG или незнакомые параметры | Изучите параметры GnuPG и флаги командной строки с помощью `man gpg`.
Нужно больше диагностических сведений | Перезапустите `gpg-agent` с подробным отладочным выводом: `pkill gpg-agent; gpg-agent --daemon --no-detach -v -v --debug-level advanced --homedir ~/.gnupg`
Общая проблема с YubiKey или GnuPG | Извлеките и снова вставьте YubiKey или перезапустите `gpg-agent`.
`Yubikey core error: no yubikey present` | Убедитесь, что YubiKey вставлен правильно. При подключении он должен один раз мигнуть.
`Yubikey core error: no yubikey present` не исчезает | Установите более новую версию `yubikey-personalization`, как описано в разделе [Установка программного обеспечения](#install-software).
`General key info..: [none]` в выводе card-status | Импортируйте открытый ключ.
Открытый ключ утерян | Следуйте инструкциям [Recovering lost GPG public keys from your YubiKey](https://www.nicksherlock.com/2021/08/recovering-lost-gpg-public-keys-from-your-yubikey/) (Восстановление утерянных открытых ключей GPG с YubiKey).
`gpg: decryption failed: secret key not available` | Установите GnuPG 2.x. Также убедитесь, что PIN верный и что нужный закрытый подключ есть на YubiKey.
`Yubikey core error: write error` | Возможно, YubiKey заблокирован. Установите и запустите `yubikey-personalization-gui`, чтобы разблокировать его.
`Key does not match the card's capability` | Используйте ключи RSA длиной 2048 бит.
`sign_and_send_pubkey: signing failed: agent refused operation` | Убедитесь, что `ssh-agent` заменён на `gpg-agent`, как описано в разделе [Замена агентов](#replace-agents).
`sign_and_send_pubkey: signing failed: agent refused operation` не исчезает | Выполните `gpg-connect-agent updatestartuptty /bye`.
`sign_and_send_pubkey: signing failed: agent refused operation` не исчезает после обновления стартового TTY | Укажите правильный путь к программе `pinentry` в `~/.gnupg/gpg-agent.conf`. Связанная ошибка может выглядеть как `gpg: decryption failed: No secret key`.
`sign_and_send_pubkey: signing failed: agent refused operation` с OpenSSH 8.9p1 или новее | Возможно, это известная проблема OpenSSH. Изучите [обсуждение на форуме Arch Linux](https://bbs.archlinux.org/viewtopic.php?id=274571) по ссылке.
`The agent has no identities` в выводе `ssh-add -L` | Установите и запустите `scdaemon`.
`Error connecting to agent: No such file or directory` в выводе `ssh-add -L` | Убедитесь, что UNIX-сокет агента настроен и доступен. См. раздел [Замена агентов](#replace-agents).
`Permission denied (publickey)` | Запустите SSH в подробном режиме, например `ssh -v host`, и убедитесь, что предлагается открытый ключ карты: `Offering public key: RSA SHA256:... cardno:00060123456`. Также убедитесь, что пользователь существует в целевой системе (он может отличаться от пользователя в локальной системе).
Аутентификация SSH по-прежнему не проходит | Увеличьте подробность вывода SSH, указав до трёх флагов: `ssh -vvv host`.
Аутентификация SSH по-прежнему не проходит после изучения подробного вывода клиента | На сервере остановите фоновую службу SSH, например командой `sudo systemctl stop sshd`, затем запустите `/usr/sbin/sshd -eddd` на переднем плане с подробным отладочным журналом, чтобы изучить попытку аутентификации. Учтите, что сервер не будет порождать дочерние процессы, обработает только одно подключение, и его придётся перезапускать после каждой проверки подключения SSH.
`Please insert the card with serial number ...` | См. раздел [Использование нескольких YubiKey](#using-multiple-yubikeys).
`There is no assurance this key belongs to the named user`, `encryption failed: Unusable public key` или `No public key` | Выполните `gpg --edit-key` и установите доверие владельцу `5 = I trust ultimately`.
`Need the secret key to do this` при установке доверия | Укажите доверие для идентификатора пользователя директивой `trust-key [key ID]`.
`pass insert` сообщает `There is no assurance this key belongs to the named user` и `encryption failed: Unusable public key` | Измените уровень доверия для идентификатора пользователя, как описано выше.
`gpg: ... skipped: Unusable public key`, `signing failed: Unusable secret key` или `encryption failed: Unusable public key` | Возможно, срок действия подключа истёк. Чтобы продлить или заменить подключи, выполните инструкции из раздела [Обновление ключей](#updating-keys).
Окно pinentry не появляется, и подпись SSH завершается ошибкой `agent refused operation` | Установите пакет `dbus-user-session`, затем перезапустите сеанс или систему.
Неожиданные запросы PIN при аутентификации SSH | Добавьте `disable-application piv` в `~/.gnupg/scdaemon.conf`.
`gpg: selecting card failed: No such device` или `gpg: OpenPGP card not available: No such device` | Возможно, у локальной службы `pcscd` нет прав доступа к карте. Создайте правило Polkit, показанное ниже, заменив `wheel`, если в системе используется другая группа администраторов.

Создайте файл `/etc/polkit-1/rules.d/99-pcscd.rules` со следующим содержимым:

```console
polkit.addRule(function(action, subject) {
        if (action.id == "org.debian.pcsc-lite.access_card" &&
                subject.isInGroup("wheel")) {
                return polkit.Result.YES;
        }
});

polkit.addRule(function(action, subject) {
        if (action.id == "org.debian.pcsc-lite.access_pcsc" &&
                subject.isInGroup("wheel")) {
                return polkit.Result.YES;
        }
});
```

# Альтернативные решения {#alternative-solutions}

- [vorburger/ed25519-sk.md](https://github.com/vorburger/blog/blob/main/security/ed25519-sk.md) — использование YubiKey для SSH без GnuPG
- [smlx/piv-agent](https://github.com/smlx/piv-agent) — агент SSH и GnuPG, который можно использовать с устройствами PIV
- [keytotpm](https://www.gnupg.org/documentation/manuals/gnupg/OpenPGP-Key-Management.html) — использование GnuPG с системами на базе TPM

# Дополнительные материалы {#additional-resources}

- [YubiKey Technical Manual (HTML)](https://docs.yubico.com/hardware/yubikey/yk-tech-manual/index.html)
- [YubiKey Technical Manual (PDF)](https://docs.yubico.com/hardware/yubikey/yk-tech-manual/webdocs.pdf)
- [Yubico - PGP](https://developers.yubico.com/PGP/)
- [Yubico - YubiKey Personalization](https://developers.yubico.com/yubikey-personalization/)
- [Kernel Maintainer PGP guide](https://www.kernel.org/doc/html/v6.19-rc4/process/maintainer-pgp-guide.html)
- [A Visual Explanation of GPG Subkeys (2022)](https://rgoulter.com/blog/posts/programming/2022-06-10-a-visual-explanation-of-gpg-subkeys.html)
- [lsasolutions/makegpg](https://gitlab.com/lsasolutions/makegpg)
- [Trammell Hudson - Yubikey (2020)](https://trmm.net/Yubikey)
- [Yubikey forwarding SSH keys (2019)](https://blog.onefellow.com/post/180065697833/yubikey-forwarding-ssh-keys)
- [GPG Agent Forwarding (2018)](https://mlohr.com/gpg-agent-forwarding/)
- [Stick with security: YubiKey, SSH, GnuPG, macOS (2018)](https://evilmartians.com/chronicles/stick-with-security-yubikey-ssh-gnupg-macos)
- [PGP and SSH keys on a Yubikey NEO (2015)](https://www.esev.com/blog/post/2015-01-pgp-ssh-key-on-yubikey-neo/)
- [Offline GnuPG Master Key and Subkeys on YubiKey NEO Smartcard (2014)](https://blog.josefsson.org/2014/06/23/offline-gnupg-master-key-and-subkeys-on-yubikey-neo-smartcard/)
- [Creating the perfect GPG keypair (2013)](https://alexcabal.com/creating-the-perfect-gpg-keypair/)

[^1]: Для отзыва идентификатора пользователя следует использовать [сертификаты отзыва](https://security.stackexchange.com/questions/14718/does-openpgp-key-expiration-add-to-security/79386#79386).
[^2]: Обсуждение рекомендаций NIST см. в [issue 477](https://github.com/drduh/YubiKey-Guide/issues/477).
