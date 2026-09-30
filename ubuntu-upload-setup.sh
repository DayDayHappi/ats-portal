#!/usr/bin/env bash
# ATS Portal —— Ubuntu 上传端一键配置脚本
# 作用：写入 Windows 服务的自签名证书、生成环境变量文件、测试连通性。
# 用法：bash ubuntu-upload-setup.sh
set -euo pipefail

SERVER="https://10.1.64.183:8443"
TOKEN="Jd65QXunxUnAi2NWSuij6N5i1Y_OmyI7Vo2uw6N1j3NLY9pK"
CERT_DIR="$HOME/ats"
CERT_FILE="$CERT_DIR/cert.pem"
ENV_FILE="$CERT_DIR/env.sh"

echo "[1/3] 写入自签名证书 -> $CERT_FILE"
mkdir -p "$CERT_DIR"
cat > "$CERT_FILE" <<'CERT_EOF'
-----BEGIN CERTIFICATE-----
MIIDOTCCAiGgAwIBAgIUb6MIvfMq/w62LdnX5bYUGaEwREIwDQYJKoZIhvcNAQEL
BQAwGzEZMBcGA1UEAwwQYXRzLXBvcnRhbC1sb2NhbDAeFw0yNjA5MjkwODUwNDRa
Fw0zNjA5MjYwODUwNDRaMBsxGTAXBgNVBAMMEGF0cy1wb3J0YWwtbG9jYWwwggEi
MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCw62HX1UctOK9LyWYurVzU73qd
P5/2ZaaK9sz5bXoiyfBS3jkU5Wg0gJKRZiATEZPh365dCHyclZ0bDie7wTZo6mKF
DAoZrEuXcrh4vZERT0ZdrRcpxysHlJEGqiQTGqPag+xEp94+7AcQl7r8WvUNHH6Q
5iPPtNgLANqkTUPa/iSAFabhDqTz7DR4AxnCJFNYwbgyf1mcYGOS5SOc91S1DTEc
pGnpyyG2v9alCbOLFL5oY6kNjcYcDUZhsKX4SSioDXrPcbLePTmkgnBOlnv8y10R
mZeirfZo03UVB1gDkaqt8uap6MJXgJ1r0GTOisVI40l75hvS632VtoC/atb5AgMB
AAGjdTBzMB0GA1UdDgQWBBTB+JqKxA6wBPHgNv8A/rzoe4l0MTAfBgNVHSMEGDAW
gBTB+JqKxA6wBPHgNv8A/rzoe4l0MTAPBgNVHRMBAf8EBTADAQH/MCAGA1UdEQQZ
MBeHBAoBQLeCCWxvY2FsaG9zdIcEfwAAATANBgkqhkiG9w0BAQsFAAOCAQEAhzcx
EiTvSf8ErXb86fHyN2J1dOha9Fg1b1H8GrfPwPMeDiMSpVacaszM1Ynr4fkD+5mW
qo9zFYA5h+JbeSH8IRTM3g7nkJ/y+9sttaCPwOwc+9z7GZgm0kpiZQhyACDwQwdr
kSwWIZGQL+oi+FeIf0+hdqBAfQ7TlaU7Qjfqfdv4vckulUkcmblB81LrldztsxxE
b4tEKYgJ3fvqsRHmuQs8IN3GFWl6QXy/w9j+I0GwEhmXOh5G5CekM9V6i5yu+3wx
c+MjL3Ro0f9NKJmO5lzdWN1MhdXv1WRFtZzfvKIeHMCBZuZIdwskL3QsaTu02Ixp
eOf1q2FTX2I/3iHT/g==
-----END CERTIFICATE-----
CERT_EOF

echo "[2/3] 生成环境变量文件 -> $ENV_FILE"
cat > "$ENV_FILE" <<ENV_EOF
export SSL_CERT_FILE="$CERT_FILE"
export ATS_PORTAL_TOKEN="$TOKEN"
ENV_EOF

echo "[3/3] 测试到 Windows 服务的连通性..."
# shellcheck disable=SC1090
source "$ENV_FILE"
if curl --fail --silent "$SERVER/api/health"; then
    echo ""
    echo "OK: 连通成功，HTTPS + 证书 + token 均就绪。"
else
    echo "FAIL: 无法连通 $SERVER" >&2
    echo "请检查：Windows 防火墙是否放行 TCP 8443；网络是否同一网段。" >&2
    exit 1
fi

cat <<MSG

========================================
配置完成。以后每次上传前先执行：

    source $ENV_FILE

上传示例（--server 必须放在 send 之前）：

    python3 tools/ats_upload.py --server $SERVER send --log-dir /path/to/log --report /path/to/report.json

重发未完成的队列：

    python3 tools/ats_upload.py --server $SERVER retry
========================================
MSG
