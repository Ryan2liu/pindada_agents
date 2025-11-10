"""
AI购物助手后端服务
基于 FastAPI + 通义千问 API
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import os
from openai import OpenAI
from dotenv import load_dotenv
import json
import asyncio

# 加载环境变量
load_dotenv()

# 初始化 FastAPI
app = FastAPI(
    title="AI购物助手 API",
    description="礼物推荐聊天机器人后端服务",
    version="1.0.0"
)

# 配置 CORS - 允许小程序调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境需要限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化通义千问客户端
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# 数据模型
class Message(BaseModel):
    """单条消息"""
    role: str  # 'user' 或 'assistant'
    content: str

class ChatRequest(BaseModel):
    """聊天请求"""
    message: str  # 用户当前输入
    history: Optional[List[Message]] = []  # 历史对话

class ChatResponse(BaseModel):
    """聊天响应"""
    response: str  # AI回复
    suggestions: Optional[List[str]] = []  # 建议的快捷回复


# 系统提示词 - 定义AI助手的角色
SYSTEM_PROMPT = """你是一个专业的礼物推荐顾问，名字叫"品答答"。你的任务是通过对话帮助用户找到最合适的礼物。

你需要：
1. 友好、热情地与用户交流
2. 通过提问收集信息：送礼对象、场合、预算、对方喜好等
3. 根据收集的信息，推荐合适的礼物
4. 回答要简洁、有条理，适当使用emoji让对话更生动
5. 如果用户提供的信息不够，主动追问关键信息

礼物推荐范围包括：
- 数码产品：耳机、手表、键盘等
- 美妆护肤：口红、香水、护肤套装
- 时尚配饰：包包、首饰、围巾
- 运动装备：球鞋、运动包、健身器材
- 创意礼物：定制礼物、手工制品、纪念品

请保持回复简洁（100字以内），不要过于冗长。"""


@app.get("/")
async def root():
    """健康检查接口"""
    return {
        "status": "ok",
        "message": "AI购物助手后端服务运行中",
        "version": "1.0.0"
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    AI对话接口（非流式）

    接收用户消息和历史对话，返回AI回复和建议回复
    """
    try:
        # 构建对话历史
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # 添加历史消息（最多保留最近10轮对话）
        if request.history:
            recent_history = request.history[-20:]  # 保留最近20条消息（10轮对话）
            for msg in recent_history:
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })

        # 添加当前用户消息
        messages.append({
            "role": "user",
            "content": request.message
        })

        # 调用通义千问 API
        completion = client.chat.completions.create(
            model="qwen-max",  # qwen-max: 最强推理能力
            messages=messages,
            temperature=0.8,  # 控制回复的创造性，0-2之间
            max_tokens=500,   # 限制回复长度
        )

        # 提取AI回复
        ai_response = completion.choices[0].message.content

        # 生成建议回复（根据对话内容智能生成）
        suggestions = generate_suggestions(request.message, ai_response)

        return ChatResponse(
            response=ai_response,
            suggestions=suggestions
        )

    except Exception as e:
        print(f"Error in chat endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"AI服务异常: {str(e)}")


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    AI对话接口（流式输出）

    使用SSE格式逐字返回AI回复，实现打字机效果
    """
    async def generate():
        try:
            # 构建对话历史
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

            # 添加历史消息
            if request.history:
                recent_history = request.history[-20:]
                for msg in recent_history:
                    messages.append({
                        "role": msg.role,
                        "content": msg.content
                    })

            # 添加当前用户消息
            messages.append({
                "role": "user",
                "content": request.message
            })

            # 调用通义千问 API（流式）
            stream = client.chat.completions.create(
                model="qwen-max",  # qwen-max: 最强推理能力
                messages=messages,
                temperature=0.8,
                max_tokens=500,
                stream=True,  # 开启流式输出
            )

            full_response = ""

            # 逐块返回数据
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response += content

                    # 按照SSE格式返回数据
                    data = {
                        "type": "content",
                        "content": content,
                        "full_text": full_response
                    }
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    # 添加小延迟，让前端有时间处理
                    await asyncio.sleep(0.01)

            # 发送完成标记和建议回复
            suggestions = generate_suggestions(request.message, full_response)
            final_data = {
                "type": "done",
                "full_text": full_response,
                "suggestions": suggestions
            }
            yield f"data: {json.dumps(final_data, ensure_ascii=False)}\n\n"

        except Exception as e:
            error_data = {
                "type": "error",
                "message": f"AI服务异常: {str(e)}"
            }
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用nginx缓冲
        }
    )


def generate_suggestions(user_message: str, ai_response: str) -> List[str]:
    """
    根据对话内容生成建议的快捷回复
    """
    suggestions = []

    # 基于关键词生成建议
    msg_lower = user_message.lower()

    if "推荐" in user_message or "建议" in user_message:
        suggestions = ["看看具体商品", "预算可以调整", "还有其他选择吗"]
    elif "预算" in user_message or "价格" in user_message:
        suggestions = ["这个价位不错", "能便宜点吗", "我想看看推荐"]
    elif "男朋友" in user_message or "女朋友" in user_message:
        suggestions = ["告诉你更多喜好", "看看推荐吧", "预算500左右"]
    elif "生日" in user_message or "纪念日" in user_message:
        suggestions = ["想要惊喜感", "实用性为主", "看看推荐"]
    else:
        # 默认建议
        suggestions = ["我想看看推荐", "还有其他的吗", "这些不错"]

    return suggestions


@app.get("/health")
async def health_check():
    """详细健康检查"""
    api_key_status = "已配置" if os.getenv("DASHSCOPE_API_KEY") else "未配置"

    return {
        "status": "healthy",
        "api_key": api_key_status,
        "model": "qwen-plus",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn

    # 启动服务
    print("🚀 启动 AI购物助手后端服务...")
    print("📍 服务地址: http://localhost:8000")
    print("📖 API文档: http://localhost:8000/docs")
    print("🔑 API Key 状态:", "已配置" if os.getenv("DASHSCOPE_API_KEY") else "未配置")

    uvicorn.run(
        "main:app",  # 使用字符串导入以支持 reload
        host="0.0.0.0",
        port=8000,
        reload=True  # 开发模式：代码修改自动重启
    )
