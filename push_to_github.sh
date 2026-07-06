#!/bin/bash
# 一键推送到GitHub的脚本
# 使用方法：
#   1. 先在 https://github.com 注册账号
#   2. 在GitHub上创建一个 Private 仓库，名字叫 ai-quant
#   3. 运行这个脚本：bash push_to_github.sh 你的GitHub用户名

USERNAME=${1:?"请提供你的GitHub用户名：bash push_to_github.sh 你的用户名"}

REPO_URL="https://github.com/${USERNAME}/ai-quant.git"

echo "=========================================="
echo "  A股AI量化系统 → GitHub 一键推送"
echo "=========================================="
echo ""
echo "目标仓库: ${REPO_URL}"
echo ""

# 添加远程仓库
git remote add origin "${REPO_URL}" 2>/dev/null || git remote set-url origin "${REPO_URL}"

# 推送代码
echo "正在推送代码到GitHub..."
git push -u origin main

echo ""
echo "=========================================="
echo "  推送完成！"
echo "=========================================="
echo ""
echo "接下来你需要做："
echo ""
echo "1. 打开 https://github.com/${USERNAME}/ai-quant/actions"
echo "   看到黄色圆圈 = 正在运行，绿色勾 = 成功"
echo ""
echo "2. 打开 GitHub Pages 仪表盘（需要手动启用）："
echo "   → 仓库 Settings → Pages → Source 选 'GitHub Actions'"
echo "   → 仪表盘地址: https://${USERNAME}.github.io/ai-quant/dashboard.html"
echo ""
echo "3. 每个交易日 15:35 自动运行，你什么都不用管"
echo ""
echo "4. 一周后回来查看："
echo "   → 仓库里看 portfolio.json（持仓+盈亏）"
echo "   → 仪表盘看可视化面板"
echo "   → Actions 页面看每天运行日志"
echo ""
