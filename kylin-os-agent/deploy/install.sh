#!/usr/bin/env bash
# 麒麟安全运维 Agent 安装脚本（在 root 下运行）
# chmod +x install.sh && ./install.sh

set -euo pipefail

INSTALL_DIR="/opt/kylin-os-agent"
SERVICE_USER="kylinos-agent"

echo "==> 创建低权限系统用户: ${SERVICE_USER}"
if ! id "${SERVICE_USER}" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
    echo "    已创建"
else
    echo "    已存在，跳过"
fi

echo "==> 部署源码到 ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
cp -r app requirements.txt .env.example "${INSTALL_DIR}/"
mkdir -p "${INSTALL_DIR}/data"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
chmod 750 "${INSTALL_DIR}/data"

echo "==> 安装 Python 依赖"
python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "==> 安装 systemd 服务"
cp deploy/kylinos-agent.service /etc/systemd/system/kylinos-agent.service
systemctl daemon-reload
systemctl enable kylinos-agent.service

echo "==> 安装 sudoers 白名单"
cp deploy/sudoers.kylinos-agent /etc/sudoers.d/kylinos-agent
chmod 440 /etc/sudoers.d/kylinos-agent
visudo -c >/dev/null && echo "    sudoers 语法 OK"

echo
echo "==> 安装完成！"
echo "    启动:   systemctl start kylinos-agent"
echo "    状态:   systemctl status kylinos-agent"
echo "    日志:   journalctl -u kylinos-agent -f"
echo "    地址:   http://127.0.0.1:8000"
echo
echo "    注意：启动前先 cp .env.example ${INSTALL_DIR}/.env 并配置真实参数"
