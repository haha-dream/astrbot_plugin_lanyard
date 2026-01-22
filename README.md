# Lanyard 活动推送插件

通过 WebSocket 实时监听 Discord 用户的活动状态，并将更新推送到 QQ 群聊。

## 功能特性

🚀 **实时活动监听**

- 通过 WebSocket 连接到 [Lanyard API](https://github.com/Phineas/lanyard)，实时获取 Discord 活动状态
- 支持监听多种活动类型：游戏、直播、Spotify 音乐、观看、自定义状态、竞争

📱 **多群推送**

- 支持将活动推送到多个 QQ 群
- 智能活动检测，仅在活动变化时推送消息

🎨 **自然的消息格式**

- 活动自动合并为一条消息，避免刷屏
- 保留关键动词（玩、听、看）确保消息阅读流畅
- 智能前缀词处理，多个活动时只保留第一个修饰词

🔧 **灵活的配置**

- 支持活动类型过滤，只推送感兴趣的活动
- 支持多种配置格式（JSON、列表、逗号分隔等）

## 安装

1. 克隆或下载插件到 AstrBot 的 `data/plugins` 目录：

```bash
cd AstrBot/data/plugins
git clone https://github.com/haha-dream/astrbot_plugin_lanyard
```

2. 重启 AstrBot 或在 WebUI 的插件管理中重载插件

## 配置

在 AstrBot WebUI 的插件配置页面中配置以下项：

### 必需配置

| 配置项      | 说明                 | 示例                     |
| ----------- | -------------------- | ------------------------ |
| `user_id`   | Discord 用户 ID      | `123456789`              |
| `qq_groups` | 推送到的 QQ 群号列表 | `[123456789, 987654321]` |

### 可选配置

| 配置项              | 说明                           | 默认值       |
| ------------------- | ------------------------------ | ------------ |
| `lanyard_api_key`   | Lanyard KV API Key（暂未实装） | 空           |
| `enable_activities` | 启用的活动类型                 | `[]`（全部） |

### 获取 Discord 用户 ID

在 Discord 中启用开发者模式，右键点击用户头像，选择"复制用户 ID"

### 活动类型编号

`enable_activities` 配置示例：

| 编号 | 活动类型   | 说明                   |
| ---- | ---------- | ---------------------- |
| 0    | 游戏       | 玩游戏                 |
| 1    | 直播       | 在平台直播             |
| 2    | 听音乐     | Spotify 或其他音乐服务 |
| 3    | 观看       | 观看视频或直播         |
| 4    | 自定义状态 | 用户设置的自定义状态   |
| 5    | 竞争       | 竞争类游戏             |

**配置示例：**

```json
{
  "user_id": "123456789",
  "qq_groups": [123456789, 987654321],
  "lanyard_api_key": "your_api_key",
  "check_interval_seconds": 30,
  "enable_activities": [0, 2]
}
```

仅推送游戏（0）和音乐（2）活动，空列表 `[]` 表示推送所有活动。

## 使用示例

### 示例 1：单个活动

用户开始玩游戏时：

```
username 开始玩 Elden Ring 了
```

### 示例 2：多个活动

用户同时进行多个活动时：

```
username 开始玩 Minecraft、听 Levitating - Dua Lipa、看 YouTube 了
```

### 示例 3：Spotify 音乐

用户在听音乐时：

```
username 开始听 Blinding Lights - The Weeknd 了
```

### 示例 4：直播

用户正在直播时：

```
username 开始直播 Just Chatting (Playing Games) 了
```

### 示例 5：自定义状态

用户设置了自定义状态时：

```
username 开始努力编程 了
```

### 示例 6：无活动

用户没有活动时（回退到 Discord 状态）：

```
username 的 Discord 状态: offline
```

## 工作原理

1. **WebSocket 连接**
   - 插件启动时建立与 `wss://api.lanyard.rest/socket` 的 WebSocket 连接
   - 订阅指定 Discord 用户的活动更新

2. **心跳保活**
   - 定期发送心跳消息，保持连接活跃
   - 若连接断开，自动重新建立

3. **活动监听**
   - 收到 `INIT_STATE` 消息时获取初始活动状态
   - 收到 `PRESENCE_UPDATE` 消息时检测活动变化

4. **变化检测**
   - 使用 MD5 哈希对活动数据进行去重
   - 仅当活动内容实际变化时才推送消息

5. **群聊推送**
   - 将格式化后的活动信息发送到配置的 QQ 群
   - 添加零宽空格防止消息被过滤

## 技术细节

### WebSocket 协议

支持的操作码（Opcode）：

- `OP_EVENT (0)`: 事件消息（INIT_STATE 或 PRESENCE_UPDATE）
- `OP_HELLO (1)`: 连接初始化消息
- `OP_INITIALIZE (2)`: 初始化消息（订阅用户）
- `OP_HEARTBEAT (3)`: 心跳消息

### 消息存储

使用 AstrBot 的 KV 存储机制：

- `last_activity_hash`: 存储上次活动的哈希值
- `group_origins`: 存储群组的 unified_msg_origin

## 故障排除

### 连接失败

检查以下内容：

- `user_id` 配置是否正确
- 网络连接是否正常
- Lanyard 服务是否在线

### 消息未推送

检查以下内容：

- `qq_groups` 是否正确配置
- 插件是否收到活动更新（查看日志）
- `enable_activities` 过滤条件是否过于严格

### 推送延迟

WebSocket 连接通常能在 1 秒内推送消息，如遇延迟：

- 检查网络延迟
- 查看 AstrBot 日志中是否有错误

## 日志

插件在 AstrBot 日志中输出关键信息：

```
[INFO] Lanyard 插件初始化中...
[INFO] 已连接到 Lanyard WebSocket
[INFO] 已订阅用户 123456789
[DEBUG] 已发送心跳
[INFO] 已推送活动更新到群 123456789
[ERROR] WebSocket 连接失败: ...
```

## 依赖

- `websockets`: WebSocket 客户端库
- AstrBot 核心依赖

自动安装，无需额外配置。

## 许可证

MIT License

## 作者

haha-dream

## 贡献

欢迎提交 Issue 和 Pull Request！

## 相关链接

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [Lanyard API](https://github.com/Phineas/lanyard)
- [Discord Developer Portal](https://discord.com/developers/applications)
- [插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
