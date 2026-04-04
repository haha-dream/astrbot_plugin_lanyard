# Lanyard 活动推送插件

通过 Lanyard HTTP API 定时拉取 Discord Presence，并将活动变化推送到 QQ 群聊。

## 功能特性

### HTTP 轮询拉取

- 通过 `https://api.lanyard.rest/v1/users/{user_id}` 定时获取 Presence
- 支持自定义轮询间隔 `poll_interval`，默认 15 秒，最小 5 秒
- 支持通过 `http_proxy` 为轮询请求配置代理

### 活动级增量推送

- 不再把所有活动一次性整包重复发送
- 每个活动都会单独建立标识和指纹
- 只有某个活动首次出现或内容发生变化时，才会推送该活动
- 活动结束后会从已推送列表移除，后续重新出现时可再次推送

### 多类型活动支持

- 支持游戏、直播、Spotify、观看、自定义状态、竞争等常见 Discord 活动
- 支持 `enable_activities` 过滤，只推送指定活动类型

### 灵活的消息过滤

- 可按 `application_id` 排除特定应用
- 可分别排除 `large_text`、`state`、`details` 字段

### 多群推送

- 支持同时推送到多个 QQ 群
- 自动缓存群消息来源 `unified_msg_origin`
- 发送时附加零宽空格，降低消息被过滤的概率

## 安装

1. 克隆插件到 AstrBot 的 `data/plugins` 目录：

```bash
cd AstrBot/data/plugins
git clone https://github.com/haha-dream/astrbot_plugin_lanyard
```

2. 重启 AstrBot，或在 WebUI 的插件管理页重载插件

## 配置

在 AstrBot WebUI 的插件配置页面中配置以下项目。

### 必需配置

| 配置项 | 说明 | 示例 |
| --- | --- | --- |
| `user_id` | Discord 用户 ID | `123456789012345678` |
| `qq_groups` | 推送到的 QQ 群号列表 | `[123456789, 987654321]` |

### 可选配置

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `poll_interval` | HTTP 轮询间隔，单位秒，最小 5 秒 | `15` |
| `http_proxy` | HTTP 轮询请求使用的代理地址 | 空 |
| `lanyard_api_key` | Lanyard KV API Key，当前未使用 | 空 |
| `enable_activities` | 启用的活动类型列表，空列表表示全部启用 | `[]` |
| `filter_config.exclude_app_ids` | 不显示这些应用的活动 | `[]` |
| `filter_config.exclude_fields.large_text` | 不显示这些应用的 `large_text` | `[]` |
| `filter_config.exclude_fields.state` | 不显示这些应用的 `state` | `[]` |
| `filter_config.exclude_fields.details` | 不显示这些应用的 `details` | `[]` |

### 获取 Discord 用户 ID

在 Discord 中启用开发者模式后，右键用户头像并选择“复制用户 ID”。

### 活动类型编号

| 编号 | 活动类型 | 说明 |
| --- | --- | --- |
| `0` | 游戏 | 玩游戏 |
| `1` | 直播 | 在平台直播 |
| `2` | 听音乐 | Spotify 或其他音乐服务 |
| `3` | 观看 | 观看视频或直播 |
| `4` | 自定义状态 | 用户设置的自定义状态 |
| `5` | 竞争 | 竞争类活动 |

### 配置示例

```json
{
  "user_id": "123456789012345678",
  "poll_interval": 15,
  "http_proxy": "http://127.0.0.1:7890",
  "qq_groups": [123456789, 987654321],
  "enable_activities": [0, 2],
  "filter_config": {
    "exclude_app_ids": [],
    "exclude_fields": {
      "large_text": [],
      "state": [],
      "details": []
    }
  }
}
```

## 推送示例

### 单个活动

用户开始玩游戏时：

```text
username 开始玩 Elden Ring 了
```

### 同时存在多个活动

如果用户先在玩游戏，后面又开始播放 Spotify，则会分别推送：

```text
username 开始玩 Minecraft 了
username 开始听 Blinding Lights - The Weeknd 了
```

### 只有某个活动变化时

如果用户保持在玩游戏，但 Spotify 曲目切换了，则只推送 Spotify 的新内容，不会把游戏活动再发一遍：

```text
username 开始听 Levitating - Dua Lipa 了
```

### 自定义状态

用户设置自定义状态时：

```text
username 努力编程 了
```

## 工作原理

1. 插件启动后按 `poll_interval` 定时请求 Lanyard HTTP API。
2. 每次拉取到 Presence 后，会遍历当前活动列表。
3. 对每个活动生成活动 key，用于标识同一条活动会话。
4. 对每个活动生成基于最终展示文案的指纹，用于判断这条活动是否真的发生了用户可见变化。
5. 仅对首次出现或内容发生变化的活动发送消息。
6. 如果某个活动结束，会从已推送缓存中移除；它以后重新出现时会再次推送。

## 技术细节

### 活动判重策略

- 活动 key 由活动类型、活动 ID、应用 ID、名称、创建时间等信息组成
- 活动 fingerprint 基于最终展示给群聊的单条活动文案生成
- 这样即使某些被过滤字段发生变化，只要最终文案没变，就不会重复推送

### 消息来源缓存

插件使用 AstrBot 的 KV 存储缓存群的 `unified_msg_origin`：

- `group_origins`: 已缓存的群消息来源

活动已推送状态保存在内存中，插件重启后会重新建立。

## 故障排除

### 没有收到推送

- 检查 `user_id` 是否正确
- 检查 `qq_groups` 是否配置正确
- 检查目标群是否已缓存 `unified_msg_origin`
- 检查 `enable_activities` 是否把目标活动过滤掉了
- 检查 `filter_config` 是否排除了该活动的关键信息

### 推送不够及时

- 当前实现基于 HTTP 轮询，不是 WebSocket 实时订阅
- 延迟通常取决于 `poll_interval`
- 如果希望更快响应，可以适当调低 `poll_interval`，但建议不要低于 5 秒

### 日志中出现 HTTP 错误

- 检查网络连通性
- 如果宿主机需要代理访问外网，检查 `http_proxy` 配置是否正确
- 检查 Lanyard 服务状态
- 检查 AstrBot 宿主环境是否能正常访问 `api.lanyard.rest`

## 日志示例

```text
[INFO] Lanyard 插件初始化中...
[INFO] Lanyard HTTP 轮询已启动，间隔 15s
[INFO] 已推送活动更新到群 123456789
[INFO] haha-dream 有 1 个活动发生变化并已推送
[ERROR] HTTP 拉取失败，状态码 429
```

## 依赖

- `aiohttp`
- `astrbot>=4.18.3`

## 许可证

MIT License

## 相关链接

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [Lanyard API](https://github.com/Phineas/lanyard)
- [Discord Developer Portal](https://discord.com/developers/applications)
- [插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
