"""
AI购物助手后端服务
基于 FastAPI + 通义千问 API
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import uuid
import json
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
import httpx
import pymysql
from pymysql.cursors import DictCursor
from openai import OpenAI
from dotenv import load_dotenv
import asyncio

# 加载环境变量
load_dotenv()

# 数据库配置
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "ai_advisor")

# 微信小程序配置
WECHAT_APPID = os.getenv("WECHAT_APPID", "")
WECHAT_SECRET = os.getenv("WECHAT_SECRET", "")

ACCESS_TOKEN_EXPIRES_DAYS = int(os.getenv("ACCESS_TOKEN_EXPIRES_DAYS", "7"))
REFRESH_TOKEN_EXPIRES_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRES_DAYS", "30"))

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

class WechatProfile(BaseModel):
    nickName: Optional[str] = None
    avatarUrl: Optional[str] = None
    gender: Optional[int] = None
    country: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    language: Optional[str] = None

class WechatLoginRequest(BaseModel):
    code: str
    profile: Optional[WechatProfile] = None

class WechatLoginResponse(BaseModel):
    token: str
    userId: int
    expiredAt: int
    profile: Optional[WechatProfile] = None

class UpdateProfileRequest(BaseModel):
    nickName: Optional[str] = None
    avatarUrl: Optional[str] = None

class ProductResponse(BaseModel):
    """商品响应模型"""
    id: int
    name: str
    image: Optional[str] = None
    brand_id: Optional[int] = None
    category_id: Optional[int] = None
    description: Optional[str] = None

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


def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=DictCursor,
        charset="utf8mb4",
        autocommit=True,
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def fetch_code2session(code: str) -> dict:
    if not WECHAT_APPID or not WECHAT_SECRET:
        raise HTTPException(status_code=500, detail="微信AppID或Secret未配置")

    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": WECHAT_APPID,
        "secret": WECHAT_SECRET,
        "js_code": code,
        "grant_type": "authorization_code",
    }

    async with httpx.AsyncClient(timeout=8.0) as client_http:
        resp = await client_http.get(url, params=params)
        data = resp.json()

    if "errcode" in data and data.get("errcode") != 0:
        raise HTTPException(status_code=400, detail=f"code2Session失败: {data}")

    return data


def get_or_create_user(openid: str, unionid: Optional[str], profile: Optional[WechatProfile], request: Request) -> int:
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT users.id AS user_id
                FROM user_auths
                JOIN users ON users.id = user_auths.user_id
                WHERE user_auths.provider = %s AND user_auths.provider_user_id = %s
                """,
                ("wechat", openid),
            )
            row = cursor.fetchone()

            if row:
                user_id = row["user_id"]
            else:
                user_uuid = str(uuid.uuid4())
                nickname = profile.nickName if profile else None
                avatar = profile.avatarUrl if profile else None
                profile_json = json.dumps(profile.dict(exclude_none=True)) if profile else None
                cursor.execute(
                    """
                    INSERT INTO users (uuid, username, avatar, profile)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (user_uuid, nickname, avatar, profile_json),
                )
                user_id = cursor.lastrowid

                cursor.execute(
                    """
                    INSERT INTO user_auths (user_id, provider, provider_user_id, verified)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (user_id, "wechat", openid, 1),
                )

            if profile:
                cursor.execute(
                    """
                    UPDATE users
                    SET username = COALESCE(%s, username),
                        avatar = COALESCE(%s, avatar),
                        profile = COALESCE(%s, profile)
                    WHERE id = %s
                    """,
                    (
                        profile.nickName,
                        profile.avatarUrl,
                        json.dumps(profile.dict(exclude_none=True)),
                        user_id,
                    ),
                )

            if unionid:
                cursor.execute(
                    """
                    UPDATE user_auths
                    SET provider_user_id = provider_user_id
                    WHERE user_id = %s AND provider = %s
                    """,
                    (user_id, "wechat"),
                )

            cursor.execute(
                """
                UPDATE users
                SET last_login_at = NOW(), last_login_ip = %s
                WHERE id = %s
                """,
                (request.client.host if request.client else None, user_id),
            )

            return user_id
    finally:
        connection.close()


def create_session(user_id: int, request: Request) -> dict:
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(48)

    access_hash = hash_token(access_token)
    refresh_hash = hash_token(refresh_token)

    now = datetime.now(timezone.utc)
    refresh_expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRES_DAYS)
    access_expires_at = now + timedelta(days=ACCESS_TOKEN_EXPIRES_DAYS)

    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_sessions (
                    user_id,
                    device_id,
                    platform,
                    access_token_hash,
                    refresh_token_hash,
                    refresh_expires_at,
                    last_active_at,
                    last_ip,
                    meta
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    None,
                    "wechat",
                    access_hash,
                    refresh_hash,
                    refresh_expires_at,
                    now,
                    request.client.host if request.client else None,
                    json.dumps({"access_expires_at": int(access_expires_at.timestamp() * 1000)}),
                ),
            )

        return {
            "access_token": access_token,
            "access_expires_at": int(access_expires_at.timestamp() * 1000),
        }
    finally:
        connection.close()


def get_user_id_from_token(token: str) -> Optional[int]:
    if not token:
        return None
    token_hash = hash_token(token)
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, refresh_expires_at, meta, revoked_at
                FROM user_sessions
                WHERE access_token_hash = %s
                """,
                (token_hash,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            if row.get("revoked_at"):
                return None

            meta = row.get("meta")
            if meta:
                try:
                    meta_json = json.loads(meta)
                    access_expires_at = meta_json.get("access_expires_at")
                    if access_expires_at and int(access_expires_at) < int(datetime.now(timezone.utc).timestamp() * 1000):
                        return None
                except Exception:
                    return None

            return row.get("user_id")
    finally:
        connection.close()

@app.get("/")
async def root():
    """健康检查接口"""
    return {
        "status": "ok",
        "message": "AI购物助手后端服务运行中",
        "version": "1.0.0"
    }


@app.post("/auth/wechat/login", response_model=WechatLoginResponse)
async def wechat_login(payload: WechatLoginRequest, request: Request):
    """
    微信登录：使用 code 换取 openid，然后生成自定义登录态
    """
    data = await fetch_code2session(payload.code)
    openid = data.get("openid")
    unionid = data.get("unionid")

    if not openid:
        raise HTTPException(status_code=400, detail="未获取到openid")

    user_id = get_or_create_user(openid, unionid, payload.profile, request)
    session_data = create_session(user_id, request)

    return WechatLoginResponse(
        token=session_data["access_token"],
        userId=user_id,
        expiredAt=session_data["access_expires_at"],
        profile=payload.profile
    )


@app.post("/auth/profile")
async def update_profile(payload: UpdateProfileRequest, request: Request):
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少授权信息")

    token = auth_header.replace("Bearer ", "").strip()
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="授权已失效")

    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            profile_json = None
            if payload.nickName or payload.avatarUrl:
                profile_json = json.dumps(
                    {
                        "nickName": payload.nickName,
                        "avatarUrl": payload.avatarUrl,
                    },
                    ensure_ascii=False,
                )
            cursor.execute(
                """
                UPDATE users
                SET username = COALESCE(%s, username),
                    avatar = COALESCE(%s, avatar),
                    profile = COALESCE(%s, profile)
                WHERE id = %s
                """,
                (payload.nickName, payload.avatarUrl, profile_json, user_id),
            )

        return {"status": "ok"}
    finally:
        connection.close()


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


@app.get("/products")
async def get_products(
    page: int = 1,
    limit: int = 20,
    category_id: Optional[int] = None,
    brand_id: Optional[int] = None
):
    """
    获取商品列表（分页）

    参数:
    - page: 页码（从1开始）
    - limit: 每页数量
    - category_id: 分类ID筛选
    - brand_id: 品牌ID筛选
    """
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 构建查询条件
            where_clauses = ["status = 1"]  # 只查询上架的商品
            params = []

            if category_id:
                where_clauses.append("category_id = %s")
                params.append(category_id)

            if brand_id:
                where_clauses.append("brand_id = %s")
                params.append(brand_id)

            where_sql = " AND ".join(where_clauses)

            # 查询总数
            count_sql = f"SELECT COUNT(*) as total FROM products WHERE {where_sql}"
            cursor.execute(count_sql, params)
            total = cursor.fetchone()['total']

            # 查询商品列表
            offset = (page - 1) * limit
            list_sql = f"""
                SELECT
                    product_id as id,
                    spu_name as name,
                    main_image_url as image,
                    brand_id,
                    category_id,
                    description
                FROM products
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(list_sql, params + [limit, offset])
            products = cursor.fetchall()

            return {
                "success": True,
                "data": {
                    "items": products,
                    "total": total,
                    "page": page,
                    "limit": limit,
                    "pages": (total + limit - 1) // limit
                }
            }
    except Exception as e:
        print(f"Error fetching products: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取商品列表失败: {str(e)}")
    finally:
        connection.close()


@app.get("/products/featured")
async def get_featured_products(limit: int = 6):
    """
    获取精选商品（随机返回）

    参数:
    - limit: 返回数量（默认6个）
    """
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql = """
                SELECT
                    product_id as id,
                    spu_name as name,
                    main_image_url as image,
                    brand_id,
                    category_id,
                    description
                FROM products
                WHERE status = 1 AND main_image_url IS NOT NULL AND main_image_url != ''
                ORDER BY RAND()
                LIMIT %s
            """
            cursor.execute(sql, (limit,))
            products = cursor.fetchall()

            return {
                "success": True,
                "data": products
            }
    except Exception as e:
        print(f"Error fetching featured products: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取精选商品失败: {str(e)}")
    finally:
        connection.close()


@app.get("/products/sections")
async def get_product_sections():
    """
    获取分组商品（用于发现页）
    返回多个商品分组，每组8个商品
    """
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sections = []

            # 分组1: 最新商品（按创建时间倒序）
            cursor.execute("""
                SELECT
                    product_id as id,
                    spu_name as name,
                    main_image_url as image,
                    brand_id,
                    category_id
                FROM products
                WHERE status = 1 AND main_image_url IS NOT NULL AND main_image_url != ''
                ORDER BY created_at DESC
                LIMIT 8
            """)
            sections.append({
                "id": "new",
                "title": "最新上架",
                "products": cursor.fetchall()
            })

            # 分组2: 随机推荐1
            cursor.execute("""
                SELECT
                    product_id as id,
                    spu_name as name,
                    main_image_url as image,
                    brand_id,
                    category_id
                FROM products
                WHERE status = 1 AND main_image_url IS NOT NULL AND main_image_url != ''
                ORDER BY RAND()
                LIMIT 8
            """)
            sections.append({
                "id": "trending",
                "title": "热门推荐",
                "products": cursor.fetchall()
            })

            # 分组3: 随机推荐2
            cursor.execute("""
                SELECT
                    product_id as id,
                    spu_name as name,
                    main_image_url as image,
                    brand_id,
                    category_id
                FROM products
                WHERE status = 1 AND main_image_url IS NOT NULL AND main_image_url != ''
                ORDER BY RAND()
                LIMIT 8
            """)
            sections.append({
                "id": "luxury",
                "title": "精选好物",
                "products": cursor.fetchall()
            })

            # 分组4: 随机推荐3
            cursor.execute("""
                SELECT
                    product_id as id,
                    spu_name as name,
                    main_image_url as image,
                    brand_id,
                    category_id
                FROM products
                WHERE status = 1 AND main_image_url IS NOT NULL AND main_image_url != ''
                ORDER BY RAND()
                LIMIT 8
            """)
            sections.append({
                "id": "digital",
                "title": "数码科技",
                "products": cursor.fetchall()
            })

            # 分组5: 随机推荐4
            cursor.execute("""
                SELECT
                    product_id as id,
                    spu_name as name,
                    main_image_url as image,
                    brand_id,
                    category_id
                FROM products
                WHERE status = 1 AND main_image_url IS NOT NULL AND main_image_url != ''
                ORDER BY RAND()
                LIMIT 8
            """)
            sections.append({
                "id": "beauty",
                "title": "美妆护肤",
                "products": cursor.fetchall()
            })

            return {
                "success": True,
                "data": sections
            }
    except Exception as e:
        print(f"Error fetching product sections: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取商品分组失败: {str(e)}")
    finally:
        connection.close()


@app.get("/products/{product_id}")
async def get_product_detail(product_id: int):
    """
    获取商品详情（包含购买链接）
    """
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 查询商品基本信息（包含品牌名称）
            sql = """
                SELECT
                    p.product_id as id,
                    p.spu_name as name,
                    p.main_image_url as image,
                    p.brand_id,
                    b.brand_name_zh as brand,
                    p.category_id,
                    p.description,
                    p.model_number,
                    p.launch_date,
                    p.status
                FROM products p
                LEFT JOIN brands b ON p.brand_id = b.brand_id
                WHERE p.product_id = %s
            """
            cursor.execute(sql, (product_id,))
            product = cursor.fetchone()

            if not product:
                raise HTTPException(status_code=404, detail="商品不存在")

            # 查询该商品的所有购买链接
            link_sql = """
                SELECT
                    link_id,
                    platform,
                    original_url,
                    affiliate_long_url,
                    affiliate_short_url,
                    conversion_status
                FROM product_affiliate_links
                WHERE product_id = %s AND conversion_status = 'success'
                ORDER BY created_at DESC
            """
            cursor.execute(link_sql, (product_id,))
            links = cursor.fetchall()

            # 将链接添加到商品信息中
            product['affiliate_links'] = links

            # 如果有链接，设置默认购买链接为第一个成功的链接
            if links and len(links) > 0:
                product['buy_url'] = links[0].get('affiliate_long_url')
                product['buy_platform'] = links[0].get('platform')
            else:
                product['buy_url'] = None
                product['buy_platform'] = None

            return {
                "success": True,
                "data": product
            }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching product detail: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取商品详情失败: {str(e)}")
    finally:
        connection.close()


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
