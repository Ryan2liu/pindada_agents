# AI购物助手后端服务

基于 FastAPI + 通义千问 API 的礼物推荐聊天机器人后端

## 功能特性

- ✅ AI 智能对话（通义千问 qwen-plus）
- ✅ 多轮对话历史管理
- ✅ 智能快捷回复建议
- ✅ CORS 支持（可供小程序调用）
- ✅ RESTful API 设计

## 技术栈

- **框架**: FastAPI
- **AI模型**: 阿里云通义千问 (Qwen)
- **服务器**: Uvicorn
- **环境管理**: python-dotenv

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r ../requirements.txt
```

### 2. 配置环境变量

已自动配置好 `.env` 文件，包含：
- `DASHSCOPE_API_KEY`: 通义千问 API Key（已从环境变量导入）

### 3. 启动服务

```bash
python main.py
```

服务将在 `http://localhost:8000` 启动

### 4. 查看 API 文档

启动后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## API 接口

### 1. 健康检查

```
GET /
GET /health
```

### 2. AI 对话接口

```
POST /chat
```

**请求体**:
```json
{
  "message": "我想给女朋友买生日礼物",
  "history": [
    {
      "role": "user",
      "content": "你好"
    },
    {
      "role": "assistant",
      "content": "你好！我是品答答，很高兴为你推荐礼物"
    }
  ]
}
```

**响应**:
```json
{
  "response": "送女朋友生日礼物，可以考虑...",
  "suggestions": ["告诉你更多喜好", "看看推荐吧", "预算500左右"]
}
```

## 项目结构

```
backend/
├── main.py          # FastAPI 主应用
├── .env             # 环境变量配置（已配置）
├── .env.example     # 环境变量示例
└── README.md        # 本文档
```

## 开发说明

### 修改模型

在 `main.py` 中修改模型参数：

```python
completion = client.chat.completions.create(
    model="qwen-plus",  # 可选: qwen-max, qwen-plus, qwen-turbo
    temperature=0.8,    # 控制创造性 (0-2)
    max_tokens=500,     # 最大回复长度
)
```

### 自定义系统提示词

在 `main.py` 中修改 `SYSTEM_PROMPT` 变量来调整 AI 助手的行为。

## 测试

### 使用 curl 测试

```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "我想给女朋友买生日礼物",
    "history": []
  }'
```

### 使用 Python 测试

```python
import requests

response = requests.post(
    "http://localhost:8000/chat",
    json={
        "message": "推荐一个500元左右的生日礼物",
        "history": []
    }
)

print(response.json())
```

## 部署

### 生产环境运行

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Docker 部署（可选）

```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY backend/ .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 常见问题

**Q: API Key 未配置？**
A: 检查 `.env` 文件中的 `DASHSCOPE_API_KEY` 是否正确配置

**Q: 小程序无法调用？**
A: 确保 CORS 配置正确，生产环境需要在 `allow_origins` 中指定小程序域名

**Q: 想要更智能的回复？**
A: 可以切换到 `qwen-max` 模型，或调整 `temperature` 参数

## 下一步计划

- [ ] 实现 Agent 工作流（LangGraph）
- [ ] 添加商品数据库
- [ ] 实现用户认证
- [ ] 对话历史持久化
- [ ] 添加监控和日志

## 许可证

MIT
