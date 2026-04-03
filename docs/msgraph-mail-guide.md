# Microsoft Graph SDK — 使用 Access Token 拉取邮件

## 1. 核心原理

SDK 通过 `TokenCredential.get_token()` 接口获取 token，发请求前自动调用。你只需提供一个实现该接口的 credential 对象，**无需 tenant_id、client_id**。

## 2. 完整代码示例

### 2.1 基础配置

```python
import asyncio
from azure.core.credentials import TokenCredential, AccessToken
from msgraph import GraphServiceClient

# 自定义 Credential：直接接收 access token
class StaticTokenCredential(TokenCredential):
    def __init__(self, access_token: str):
        self._token = access_token

    def get_token(self, *scopes, **kwargs):
        # expires_on 设为 0 表示永不过期（token 本身已包含有效期信息）
        return AccessToken(self._token, 0)

# 初始化客户端
credential = StaticTokenCredential("your-access-token")
scopes = ["Mail.Read", "User.Read"]  # 根据你的 token 权限范围调整
client = GraphServiceClient(credentials=credential, scopes=scopes)
```

### 2.2 获取当前用户信息

```python
async def get_me():
    user = await client.me.get()
    print(f"Display Name: {user.display_name}")
    print(f"Email: {user.mail}")
    print(f"User ID: {user.id}")

asyncio.run(get_me())
```

### 2.3 获取邮件列表

```python
async def get_messages(top: int = 10):
    # 获取前 10 封邮件（默认最多 100 封）
    messages = await client.me.messages.get()
    if messages and messages.value:
        for msg in messages.value[:top]:
            print(f"From: {msg.sender}")
            print(f"Subject: {msg.subject}")
            print(f"Received: {msg.received_date_time}")
            print(f"Read: {msg.is_read}")
            print("---")

asyncio.run(get_messages())
```

### 2.4 获取指定邮件详情

```python
async def get_message(message_id: str):
    msg = await client.me.messages.by_message_id(message_id).get()
    print(f"Subject: {msg.subject}")
    print(f"Body Preview: {msg.body_preview}")
    print(f"From: {msg.sender}")
    print(f"Body: {msg.body.content}")  # HTML or text

asyncio.run(get_message("message-id-here"))
```

### 2.5 分页获取邮件

```python
async def get_all_messages():
    messages = await client.me.messages.get()
    while messages:
        for msg in messages.value:
            print(f"{msg.received_date_time} - {msg.subject}")
        # 下一批
        if messages.odata_next_link:
            messages = await client.me.messages.with_url(messages.odata_next_link).get()
        else:
            break

asyncio.run(get_all_messages())
```

### 2.6 按邮件夹获取（收件箱等）

```python
async def get_inbox():
    # 先获取邮箱文件夹列表
    folders = await client.me.mail_folders.get()
    for folder in folders.value:
        print(f"{folder.display_name} ({folder.total_message_count})")

    # 获取收件箱 (inbox) 的邮件
    inbox_messages = await client.me.mail_folders.by_mail_folder_id("inbox").messages.get()
    for msg in inbox_messages.value:
        print(f"{msg.subject} - {msg.received_date_time}")

asyncio.run(get_inbox())
```

### 2.7 搜索邮件

```python
async def search_messages(query: str):
    from msgraph.generated.users.item.messages.messages_request_builder import (
        MessagesRequestBuilder
    )
    query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
        search=[query],       # 搜索主题或内容
        top=20,
        orderby=["receivedDateTime desc"]
    )
    request_config = MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration(
        query_parameters=query_params
    )
    messages = await client.me.messages.get(request_configuration=request_config)
    for msg in messages.value:
        print(f"{msg.subject} - {msg.received_date_time}")

asyncio.run(search_messages("meeting"))
```

## 3. API 端点对照表

| 操作 | SDK 调用 | REST API |
|------|---------|----------|
| 列出邮件 | `client.me.messages.get()` | `GET /me/messages` |
| 获取单封邮件 | `client.me.messages.by_message_id(id).get()` | `GET /me/messages/{id}` |
| 收件箱 | `client.me.mail_folders.by_mail_folder_id("inbox").messages.get()` | `GET /me/mailFolders/inbox/messages` |
| 发送邮件 | `client.me.send_mail.post(...)` | `POST /me/sendMail` |
| 删除邮件 | `client.me.messages.by_message_id(id).delete()` | `DELETE /me/messages/{id}` |
| 标记已读 | `client.me.messages.by_message_id(id).patch(...)` | `PATCH /me/messages/{id}` |

## 4. 支持的邮件夹 ID

| 邮件夹 | ID |
|--------|-----|
| 收件箱 | `inbox` |
| 草稿箱 | `drafts` |
| 已发送 | `sentitems` |
| 已删除 | `deleteditems` |
| 垃圾邮件 | `junkemail` |
| 存档 | `archive` |

## 5. 常见问题

### Q: token 过期了怎么办？

A: 自行刷新 token，然后将新 token 更新到 `StaticTokenCredential._token`，重新创建 client 即可。

### Q: token 权限不够？

A: 确保 token 包含 `Mail.Read`（读邮件）、`Mail.Send`（发邮件）等 scope。如果用 `/.default` scope，需要在 Azure AD 应用权限中勾选对应权限。

### Q: 如何处理错误？

```python
from kiota_abstractions.api_error import APIError

async def safe_get_messages():
    try:
        messages = await client.me.messages.get()
        return messages
    except APIError as e:
        print(f"Error: {e.error.code} - {e.error.message}")
```

## 6. 所需依赖

```bash
pip install msgraph-sdk azure-identity
```

> **注意**：本 SDK 为异步 API（基于 `asyncio`），所有操作需使用 `async/await` 语法，并通过 `asyncio.run()` 执行入口函数。
