#!/bin/bash
#
# Alloy 完整清除腳本
# 用途：移除所有與 Alloy 相關的檔案和設定，以便全新重新安裝
# 執行方式：sudo bash uninstall_alloy.sh
#

# 確保以 root 權限執行
if [ "$(id -u)" != "0" ]; then
   echo "錯誤: 此腳本必須以 root 權限執行" 
   echo "請使用: sudo bash uninstall_alloy.sh"
   exit 1
fi

echo "===== Alloy 清除程序開始 ====="
echo "此腳本將移除所有 Alloy 相關的組件，包括:"
echo "- Alloy 服務"
echo "- Alloy 二進制文件"
echo "- Alloy 配置文件"
echo "- Alloy 系統用戶"
echo "- Alloy 系統群組"
echo "- Alloy 相關日誌"

# 詢問確認
read -p "確定要繼續嗎? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "取消操作"
    exit 0
fi

echo -e "\n1) 停止和禁用 Alloy 服務"
if systemctl is-active --quiet alloy; then
    systemctl stop alloy
    echo "✅ Alloy 服務已停止"
else
    echo "⚠️ Alloy 服務未運行"
fi

if systemctl is-enabled --quiet alloy 2>/dev/null; then
    systemctl disable alloy
    echo "✅ Alloy 服務已禁用"
else
    echo "⚠️ Alloy 服務未啟用"
fi

echo -e "\n2) 移除 systemd 服務單元文件"
if [ -f /etc/systemd/system/alloy.service ]; then
    rm /etc/systemd/system/alloy.service
    echo "✅ Alloy 服務單元文件已移除"
else
    echo "⚠️ Alloy 服務單元文件不存在"
fi

# 重新載入 systemd
systemctl daemon-reload
echo "✅ systemd 已重新載入"

echo -e "\n3) 移除 Alloy 二進制文件"
if [ -f /usr/local/bin/alloy ]; then
    rm /usr/local/bin/alloy
    echo "✅ Alloy 二進制文件已移除"
else
    echo "⚠️ Alloy 二進制文件不存在於 /usr/local/bin"
fi

# 檢查其他可能的安裝位置
if [ -f /usr/bin/alloy ]; then
    rm /usr/bin/alloy
    echo "✅ Alloy 二進制文件已從 /usr/bin 移除"
fi

echo -e "\n4) 移除 Alloy 配置目錄和文件"
if [ -d /etc/alloy ]; then
    rm -rf /etc/alloy
    echo "✅ Alloy 配置目錄已移除"
else
    echo "⚠️ Alloy 配置目錄不存在"
fi

# 移除環境變數檔
if [ -f /etc/default/alloy ]; then
    rm /etc/default/alloy
    echo "✅ Alloy 環境變數檔已移除"
else
    echo "⚠️ Alloy 環境變數檔不存在"
fi

echo -e "\n5) 移除 Alloy 系統用戶和群組"
if id -u alloy >/dev/null 2>&1; then
    userdel alloy 2>/dev/null
    echo "✅ Alloy 系統用戶已移除"
else
    echo "⚠️ Alloy 系統用戶不存在"
fi

if getent group alloy >/dev/null 2>&1; then
    groupdel alloy 2>/dev/null
    echo "✅ Alloy 系統群組已移除"
else
    echo "⚠️ Alloy 系統群組不存在"
fi

echo -e "\n6) 清除 Alloy 日誌"
if [ -d /var/log/alloy ]; then
    rm -rf /var/log/alloy
    echo "✅ Alloy 日誌目錄已移除"
else
    echo "⚠️ Alloy 日誌目錄不存在"
fi

# 清除 journald 日誌 (可選)
if command -v journalctl >/dev/null 2>&1; then
    echo "清除 journald 中的 Alloy 日誌..."
    journalctl --vacuum-time=1s -u alloy 2>/dev/null
    echo "✅ Journald 日誌已清除"
fi

echo -e "\n===== Alloy 清除完成 ====="
echo "系統已準備好重新安裝 Alloy"
echo "執行安裝指令: sudo bash install_alloy.sh"
