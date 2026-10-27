#!/usr/bin/env bash
# MexAy yedek geri yükleme testi.
#
# Kullanım:
#   TARGET_DATABASE_URL='postgresql://...boş-test-veritabanı' \
#   [SOURCE_DATABASE_URL='postgresql://...üretim (yalnızca okunur sayım için)'] \
#   ./scripts/restore_test.sh yedekler/mexay_YYYYMMDD_HHMMSS.dump
#
# Güvenlik kuralları:
# - TARGET veritabanı BOŞ olmalı (public şemasında tablo varsa betik durur).
# - TARGET adresi SOURCE/DATABASE_URL ile aynıysa betik durur: üretime geri yükleme yapılmaz.
# - Bağlantı adresleri ekrana yazdırılmaz.
# - SOURCE verilirse yalnızca SELECT count(*) çalıştırılır; üretimde yazma yapılmaz.

set -euo pipefail
umask 077

DUMP="${1:-}"
if [[ -z "$DUMP" || ! -s "$DUMP" ]]; then
  echo "Kullanım: TARGET_DATABASE_URL=... $0 <yedek.dump>" >&2; exit 2
fi
if [[ -z "${TARGET_DATABASE_URL:-}" ]]; then
  echo "HATA: TARGET_DATABASE_URL tanımlı değil (boş bir TEST veritabanı olmalı)." >&2; exit 2
fi
for tool in pg_restore psql sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || { echo "HATA: '$tool' bulunamadı." >&2; exit 2; }
done
if [[ "$TARGET_DATABASE_URL" == "${DATABASE_URL:-__yok__}" || "$TARGET_DATABASE_URL" == "${SOURCE_DATABASE_URL:-__yok__}" ]]; then
  echo "HATA: Hedef, üretim veritabanıyla aynı görünüyor. Durduruldu." >&2; exit 3
fi

# Bütünlük: .sha256 dosyası varsa kontrol et.
if [[ -f "$DUMP.sha256" ]]; then
  ( cd "$(dirname "$DUMP")" && sha256sum -c "$(basename "$DUMP").sha256" ) || { echo "HATA: SHA-256 uyuşmuyor." >&2; exit 1; }
fi

pg_restore --list "$DUMP" >/dev/null || { echo "HATA: Arşiv okunamadı." >&2; exit 1; }

EXISTING="$(psql "$TARGET_DATABASE_URL" -Atqc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
if [[ "$EXISTING" != "0" ]]; then
  echo "HATA: Hedef veritabanı boş değil ($EXISTING tablo var). Boş bir test veritabanı kullanın." >&2; exit 3
fi

echo "Geri yükleniyor..."
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$TARGET_DATABASE_URL" "$DUMP"

TABLES=(businesses users customers appointments messages subscriptions audit_logs)
echo
printf '%-16s %10s %10s\n' "tablo" "yedek" "üretim"
FAIL=0
for t in "${TABLES[@]}"; do
  RESTORED="$(psql "$TARGET_DATABASE_URL" -Atqc "SELECT count(*) FROM public.$t" 2>/dev/null || echo "YOK")"
  SRC="-"
  if [[ -n "${SOURCE_DATABASE_URL:-}" ]]; then
    SRC="$(psql "$SOURCE_DATABASE_URL" -Atqc "SELECT count(*) FROM public.$t" 2>/dev/null || echo "YOK")"
  fi
  printf '%-16s %10s %10s\n' "$t" "$RESTORED" "$SRC"
  [[ "$RESTORED" == "YOK" ]] && FAIL=1
  # Yedek alındıktan sonra yeni kayıt eklenmiş olabilir; yalnızca yedek > üretim ise uyar.
  if [[ "$SRC" =~ ^[0-9]+$ && "$RESTORED" =~ ^[0-9]+$ && "$RESTORED" -gt "$SRC" ]]; then
    echo "  uyarı: $t tablosunda yedek, üretimden fazla kayıt içeriyor (kayıt silinmiş olabilir)."
  fi
done

if [[ "$FAIL" -ne 0 ]]; then
  echo "BAŞARISIZ: Bazı tablolar geri yüklenemedi." >&2; exit 1
fi
echo
echo "Geri yükleme testi BAŞARILI. Test veritabanını işiniz bitince silebilirsiniz."
