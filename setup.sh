#!/bin/bash
# 量化交易系统 —— 服务器一键部署脚本
# 用法: bash setup.sh
# 适用: Tencent Cloud OpenCloudOS 8, Python 3.11

set -e
PROJECT_DIR="/root/Liang-Hua"
VENV_DIR="$PROJECT_DIR/venv"

echo "========================================="
echo "  量化交易系统 服务器部署"
echo "========================================="
echo ""

# 1. 目录结构
echo "📁 创建目录结构..."
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/data/backups"
echo "   ✅ logs/ data/backups/"

# 2. Python 虚拟环境
if [ ! -d "$VENV_DIR" ]; then
    echo "🐍 创建虚拟环境..."
    python3.11 -m venv "$VENV_DIR"
    echo "   ✅ venv/"
else
    echo "🐍 虚拟环境已存在，跳过"
fi

# 3. 安装/更新依赖
echo "📦 安装依赖..."
source "$VENV_DIR/bin/activate"
pip install -q akshare baostock pandas numpy requests
echo "   ✅ akshare baostock pandas numpy requests"

# 4. 创建 settings_local.py（如果不存在）
if [ ! -f "$PROJECT_DIR/config/settings_local.py" ]; then
    echo "🔑 创建 config/settings_local.py 模板..."
    cat > "$PROJECT_DIR/config/settings_local.py" << 'EOF'
# 服务器敏感配置 —— 部署时填入实际值
DINGTALK_WEBHOOK = "替换为实际webhook地址"
TUSHARE_TOKEN = "替换为实际token"
BARK_KEY = ""
EOF
    echo "   ⚠️ 请编辑 config/settings_local.py 填入真实 token！"
else
    echo "🔑 settings_local.py 已存在，跳过"
fi

# 5. 初始化数据库（如果不存在）
if [ ! -f "$PROJECT_DIR/data/stocks.db" ]; then
    echo "🗄 初始化数据库..."
    source "$VENV_DIR/bin/activate"
    cd "$PROJECT_DIR"
    python -c "
from data_fetcher.downloader import init_database
init_database()
print('数据库初始化完成')
"
else
    echo "🗄 数据库已存在，跳过"
fi

# 6. 配置 crontab
echo ""
echo "⏰ 当前 crontab 配置建议:"
echo "----------------------------------------"
cat << 'CRON'
# 量化交易系统定时任务
# 21:00 — 数据更新 + 信号生成 + 推送
0 21 * * 1-5 cd /root/Liang-Hua && ./venv/bin/python run.py >> logs/cron.log 2>&1

# 21:05 — 市场日报
5 21 * * 1-5 cd /root/Liang-Hua && ./venv/bin/python analysis/report.py >> logs/report.log 2>&1

# 21:10 — 健康检查（run.py 是否正常？无信号则钉钉告警）
10 21 * * 1-5 cd /root/Liang-Hua && ./venv/bin/python scripts/health_check.py >> logs/health.log 2>&1

# 21:15 — 数据库备份（保留最近7天）
15 21 * * 1-5 cd /root/Liang-Hua && ./venv/bin/python scripts/health_check.py --backup >> logs/health.log 2>&1
CRON
echo "----------------------------------------"
echo ""
echo "   手动配置: crontab -e 然后粘贴上面的内容"

# 7. 验证
echo ""
echo "🔍 验证部署..."
source "$VENV_DIR/bin/activate"
cd "$PROJECT_DIR"

# 检查关键文件
for f in "run.py" "config/settings.py" "data_fetcher/downloader.py" \
         "scripts/health_check.py" "config/settings_local.py"; do
    if [ -f "$f" ]; then
        echo "   ✅ $f"
    else
        echo "   ❌ $f 缺失"
    fi
done

# 检查 token 是否已配置
if grep -q "替换为实际" "$PROJECT_DIR/config/settings_local.py" 2>/dev/null; then
    echo ""
    echo "⚠️  ========================================="
    echo "   config/settings_local.py 中的 token 尚未配置！"
    echo "   请编辑该文件填入真实的钉钉 Webhook 和 Tushare token"
    echo "   vi $PROJECT_DIR/config/settings_local.py"
    echo "⚠️  ========================================="
fi

echo ""
echo "========================================="
echo "  ✅ 部署完成"
echo "========================================="
echo ""
echo "后续步骤:"
echo "  1. 编辑 config/settings_local.py 填入 token"
echo "  2. crontab -e 配置定时任务"
echo "  3. 手动跑一次验证: python run.py --init"
echo "  4. 检查日志: tail -f logs/cron.log"
