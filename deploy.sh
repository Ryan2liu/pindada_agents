#!/bin/bash
#
# 应用部署/更新脚本
# 在 ECS 上运行，用于快速部署或更新应用
#
# 使用方法：
#   chmod +x deploy.sh
#   ./deploy.sh
#

set -e

echo "=========================================="
echo "🚀 开始部署 AI 购物助手应用"
echo "=========================================="

# 进入项目目录
cd "$(dirname "$0")"

# 检查必要文件
echo ""
echo "📋 检查环境配置..."
if [ ! -f "backend/.env" ]; then
    echo "❌ 错误：backend/.env 文件不存在"
    echo "💡 请先复制 backend/.env.example 并配置正确的值"
    exit 1
fi

if [ ! -f "docker-compose.yml" ]; then
    echo "❌ 错误：docker-compose.yml 文件不存在"
    exit 1
fi

# 显示当前配置
echo "✅ 环境配置文件存在"

# 拉取最新代码（如果是 git 仓库）
if [ -d ".git" ]; then
    echo ""
    echo "📥 拉取最新代码..."
    git pull
fi

# 停止旧容器
echo ""
echo "🛑 停止旧容器..."
docker-compose down || true

# 构建新镜像
echo ""
echo "🔨 构建 Docker 镜像..."
docker-compose build --no-cache

# 启动容器
echo ""
echo "▶️  启动容器..."
docker-compose up -d

# 等待服务启动
echo ""
echo "⏳ 等待服务启动..."
sleep 5

# 检查容器状态
echo ""
echo "🔍 检查容器状态..."
docker-compose ps

# 测试健康检查
echo ""
echo "🏥 测试服务健康状态..."
sleep 3
curl -f http://localhost:8000/health || {
    echo "❌ 健康检查失败，查看日志："
    docker-compose logs --tail=50 app
    exit 1
}

echo ""
echo "=========================================="
echo "✅ 部署完成！"
echo "=========================================="
echo ""
echo "📊 容器状态："
docker-compose ps
echo ""
echo "📝 查看日志："
echo "   docker-compose logs -f app      # 查看应用日志"
echo "   docker-compose logs -f redis    # 查看 Redis 日志"
echo "   docker-compose logs -f postgres # 查看数据库日志"
echo ""
echo "🔧 其他命令："
echo "   docker-compose restart          # 重启所有服务"
echo "   docker-compose down             # 停止所有服务"
echo "   docker-compose exec app bash    # 进入应用容器"
echo ""
