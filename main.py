import asyncio
import json
from typing import Optional

import websockets

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain, filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_lanyard",
    "haha-dream",
    "基于 Lanyard 把你的活动推送到群聊",
    "v1.0.4",
)
class LanyardActivityNotifier(Star):
    """Lanyard 活动推送插件

    通过 WebSocket 连接 Lanyard API，监听 Discord 用户的活动状态变化，
    并将更新推送到配置的 QQ 群聊。
    """

    OP_EVENT = 0
    OP_HELLO = 1
    OP_INITIALIZE = 2
    OP_HEARTBEAT = 3

    EVENT_INIT_STATE = "INIT_STATE"
    EVENT_PRESENCE_UPDATE = "PRESENCE_UPDATE"

    LANYARD_WS_URL = "wss://api.lanyard.rest/socket"

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._last_activities = None
        self._lock = asyncio.Lock()
        self._ws = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval: float = 30.0

    async def initialize(self):
        """初始化插件，启动 WebSocket 监听"""
        user_id = str(self.config.get("user_id", "")).strip()
        if not user_id:
            logger.warning(
                "Lanyard 插件: 未配置 Discord 用户 ID，插件已禁用。请在配置中设置 user_id"
            )
            return

        logger.info("Lanyard 插件初始化中...")
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._websocket_loop())

    async def terminate(self):
        """终止插件，停止 WebSocket 连接"""
        logger.info("Lanyard 插件终止中...")
        self._stop_event.set()

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            finally:
                self._heartbeat_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

    async def _websocket_loop(self):
        """WebSocket 主循环：连接、接收消息"""
        user_id = str(self.config.get("user_id", "")).strip()
        if not user_id:
            logger.warning(
                "Lanyard 插件: 未配置 Discord 用户 ID，无法启动 WebSocket 连接"
            )
            return

        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"WebSocket 连接错误: {e}")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
                except TimeoutError:
                    pass

    async def _connect_and_listen(self):
        """建立 WebSocket 连接并监听消息"""
        user_id = str(self.config.get("user_id", "")).strip()
        if not user_id:
            logger.warning("未配置 Discord 用户 ID，跳过连接")
            return

        try:
            async with websockets.connect(
                self.LANYARD_WS_URL,
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                self._ws = ws
                logger.info("已连接到 Lanyard WebSocket")

                hello_msg = await ws.recv()
                hello_data = json.loads(hello_msg)

                if hello_data.get("op") != self.OP_HELLO:
                    logger.error("未收到 HELLO 消息")
                    return

                self._heartbeat_interval = (
                    hello_data.get("d", {}).get("heartbeat_interval", 30000) / 1000
                )

                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                init_msg = {
                    "op": self.OP_INITIALIZE,
                    "d": {"subscribe_to_ids": [user_id]},
                }
                await ws.send(json.dumps(init_msg))
                logger.info(f"已订阅用户 {user_id}")

                async for message in ws:
                    if self._stop_event.is_set():
                        break

                    try:
                        data = json.loads(message)
                        await self._handle_message(data)
                    except Exception as e:
                        logger.error(f"处理 WebSocket 消息错误: {e}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"WebSocket 连接失败: {e}")
        finally:
            self._ws = None

    async def _heartbeat_loop(self):
        """定期发送心跳"""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self._heartbeat_interval)

                if self._ws is None:
                    break

                heartbeat_msg = {"op": self.OP_HEARTBEAT}
                await self._ws.send(json.dumps(heartbeat_msg))
                logger.debug("已发送心跳")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"发送心跳失败: {e}")
                break

    async def _handle_message(self, data: dict):
        """处理 WebSocket 消息"""
        op = data.get("op")
        event_type = data.get("t")

        if op != self.OP_EVENT:
            return

        if event_type == self.EVENT_INIT_STATE:
            user_data = data.get("d", {})
            presence_data = next(iter(user_data.values())) if user_data else None
            if presence_data:
                await self._check_and_push_update(presence_data)

        elif event_type == self.EVENT_PRESENCE_UPDATE:
            presence_data = data.get("d", {})
            await self._check_and_push_update(presence_data)

    async def _check_and_push_update(self, presence_data: dict):
        """检查活动是否变化，如果变化则推送"""
        current_fingerprint = self._generate_activity_fingerprint(presence_data)

        if current_fingerprint == self._last_activities:
            return

        self._last_activities = current_fingerprint

        await self._push_update(presence_data)

    def _generate_activity_fingerprint(self, presence_data: dict) -> str:
        """生成活动指纹，用于检测活动变化"""
        activities = presence_data.get("activities", [])
        enable_activities = self._parse_enable_activities(
            self.config.get("enable_activities", [])
        )

        activity_states = []
        for activity in activities:
            activity_type = activity.get("type", 6)

            if enable_activities and activity_type not in enable_activities:
                continue

            # 特殊处理 Spotify, 使用 details 作为指纹
            if activity_type == 2:
                details = activity.get("details", "")
                if details:
                    activity_states.append(details)
            elif activity.get("state") is None:
                details = activity.get("details", "")
                if details:
                    activity_states.append(details)
            elif activity.get("details") is None:
                details = activity.get("name", "")
                if details:
                    activity_states.append(details)
            else:
                activity_state = activity.get("state", "")
                if activity_state:
                    activity_states.append(activity_state)

        fingerprint = "|".join(activity_states)
        return fingerprint

    async def _push_update(self, presence_data: dict):
        """推送活动更新到 QQ 群聊"""
        qq_groups = self._parse_qq_groups(self.config.get("qq_groups", []))
        if not qq_groups:
            logger.warning("未配置 QQ 群号，跳过推送")
            return

        text = self._format_presence(presence_data)
        if not text:
            return

        text = "\u200b" + text + "\u200b"

        for group_id in qq_groups:
            try:
                chain = MessageChain()
                chain.message(text)

                umo = await self._get_group_unified_msg_origin(group_id)
                if not umo:
                    logger.warning(
                        f"群 {group_id} 未缓存的 unified_msg_origin，跳过推送。请先在该群发送消息。"
                    )
                    continue

                await self.context.send_message(umo, chain)
                logger.info(f"已推送活动更新到群 {group_id}")
            except Exception as e:
                logger.error(f"推送到群 {group_id} 失败: {e}")

    async def _get_group_unified_msg_origin(self, group_id: str) -> Optional[str]:
        """获取群的 unified_msg_origin，优先从缓存获取"""
        async with self._lock:
            group_origins = await self.get_kv_data("group_origins", {})
            if not isinstance(group_origins, dict):
                group_origins = {}

            origin = group_origins.get(group_id)
            return origin

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def _on_group_message(self, event: AstrMessageEvent):
        """监听群消息，自动缓存群的 unified_msg_origin"""
        if not hasattr(event, "get_group_id"):
            return

        group_id = event.get_group_id()
        if not group_id:
            return

        try:
            umo = getattr(event, "unified_msg_origin", None)
            if umo:
                async with self._lock:
                    group_origins = await self.get_kv_data("group_origins", {})
                    if not isinstance(group_origins, dict):
                        group_origins = {}
                    group_origins[str(group_id)] = umo
                    await self.put_kv_data("group_origins", group_origins)
                    logger.debug(f"已缓存群 {group_id} 的消息来源")
        except Exception as e:
            logger.debug(f"缓存群消息来源失败: {e}")

    def _parse_qq_groups(self, value: object) -> set[str]:
        """解析 QQ 群号列表配置"""
        if not isinstance(value, list):
            return set()
        return {str(x).strip() for x in value if str(x).strip()}

    def _parse_enable_activities(self, value: object) -> set[int]:
        """解析启用的活动类型配置"""
        if not isinstance(value, list):
            return set()
        result = set()
        for item in value:
            try:
                result.add(int(item))
            except (ValueError, TypeError):
                pass
        return result

    def _get_filter_config(self) -> dict:
        """获取过滤配置"""
        filter_config = self.config.get("filter_config", {})
        if not isinstance(filter_config, dict):
            filter_config = {}

        # 安全地获取 exclude_app_ids
        exclude_app_ids = filter_config.get("exclude_app_ids", [])
        if not isinstance(exclude_app_ids, list):
            exclude_app_ids = []

        # 安全地处理 exclude_app_ids 中的每个元素
        safe_exclude_app_ids = set()
        for x in exclude_app_ids:
            try:
                safe_exclude_app_ids.add(str(x).strip())
            except:
                pass

        # 安全地获取 exclude_fields
        exclude_fields = filter_config.get("exclude_fields", {})
        if not isinstance(exclude_fields, dict):
            exclude_fields = {}

        # 安全地处理 exclude_fields 中的内容
        safe_exclude_fields = {}
        for field_name, app_ids in exclude_fields.items():
            if not isinstance(app_ids, list):
                continue
            safe_app_ids = set()
            for app_id in app_ids:
                try:
                    safe_app_ids.add(str(app_id).strip())
                except:
                    pass
            safe_exclude_fields[str(field_name)] = list(safe_app_ids)

        return {
            "exclude_app_ids": safe_exclude_app_ids,
            "exclude_fields": safe_exclude_fields,
        }

    def _should_include_field(
        self, activity_type: int, field_name: str, app_id: str
    ) -> bool:
        """判断字段是否应该被包含"""
        if self._should_exclude_app(app_id):
            return False

        filter_config = self._get_filter_config()
        excluded_fields = filter_config["exclude_fields"].get(field_name, [])

        # 确保 excluded_fields 是列表
        if not isinstance(excluded_fields, list):
            return True

        # 安全地检查 app_id 是否在排除列表中
        try:
            app_id_str = str(app_id).strip() if app_id else ""
            excluded_set = {str(x).strip() for x in excluded_fields if x is not None}
            return app_id_str not in excluded_set
        except Exception:
            # 如果出现任何错误，默认包含该字段
            return True

    def _should_exclude_app(self, app_id: str) -> bool:
        """判断应用是否应该被排除"""
        if not app_id:
            return False

        filter_config = self._get_filter_config()
        try:
            app_id_str = str(app_id).strip()
            return app_id_str in filter_config["exclude_app_ids"]
        except Exception:
            # 如果出现任何错误，默认不排除
            return False

    def _format_presence(self, presence_data: dict) -> str:
        """格式化活动信息为可读的文本"""
        try:
            user = presence_data.get("discord_user", {})
            username = user.get("display_name", "Unknown")

            enable_activities = self._parse_enable_activities(
                self.config.get("enable_activities", [])
            )

            activities_info = []

            activities = presence_data.get("activities", [])
            if activities:
                for activity in activities:
                    activity_type = activity.get("type", 6)

                    if enable_activities and activity_type not in enable_activities:
                        continue

                    activity_msg = self._format_activity_brief(activity)
                    if activity_msg:
                        activities_info.append(activity_msg)

            if activities_info:
                formatted_lines = []
                for activity_info in activities_info:
                    if isinstance(activity_info, tuple):
                        modifier, verb_content = activity_info
                        formatted_lines.append(
                            f"{username} {modifier}{verb_content} 了"
                        )
                    elif activity_info:
                        formatted_lines.append(f"{username} {activity_info} 了")

                if formatted_lines:
                    return "\n".join(formatted_lines)

                discord_status = presence_data.get("discord_status", "offline")
                return f"{username} 的 Discord 状态: {discord_status}"

            discord_status = presence_data.get("discord_status", "offline")
            return f"{username} 的 Discord 状态: {discord_status}"

        except Exception as e:
            logger.error(f"格式化活动信息失败: {e}")
            return None

    def _format_activity_brief(self, activity: dict) -> str | tuple:
        """格式化单个活动（返回字符串或修饰词和动词+内容的元组）"""
        try:
            activity_type = activity.get("type", 6)
            activity_name = activity.get("name", "Unknown")
            details = activity.get("details", "")
            state = activity.get("state", "")
            assets = activity.get("assets", {})
            app_id = activity.get("application_id", "")

            # 检查应用是否被排除
            if app_id and self._should_exclude_app(app_id):
                return None

            if activity_type == 0:
                game_info_parts = [f"玩 {activity_name}"]

                if self._should_include_field(activity_type, "large_text", app_id):
                    if assets and assets.get("large_text"):
                        large_text = assets["large_text"].strip()
                        game_info_parts.append(large_text)

                if self._should_include_field(activity_type, "state", app_id):
                    if state:
                        state_text = state.strip()
                        game_info_parts.append(state_text)

                if self._should_include_field(activity_type, "details", app_id):
                    if details:
                        game_info_parts.append(details)

                game_info = " | ".join(game_info_parts)
                return ("开始", game_info)

            elif activity_type == 1:
                stream_info = f"直播 {activity_name}"
                if details and self._should_include_field(
                    activity_type, "details", app_id
                ):
                    stream_info += f" ({details})"
                return ("开始", stream_info)

            elif activity_type == 2:
                # Spotify
                if (
                    details
                    and state
                    and self._should_include_field(activity_type, "details", app_id)
                    and self._should_include_field(activity_type, "state", app_id)
                ):
                    return ("开始", f"听 {details} - {state}")
                elif details and self._should_include_field(
                    activity_type, "details", app_id
                ):
                    return ("开始", f"听 {details}")
                elif state and self._should_include_field(
                    activity_type, "state", app_id
                ):
                    return ("开始", f"听 {state}")
                return ("开始", f"听 {activity_name}")

            elif activity_type == 3:
                watching_info = f"看 {activity_name}"
                if details and self._should_include_field(
                    activity_type, "details", app_id
                ):
                    watching_info += f" ({details})"
                return ("开始", watching_info)

            elif activity_type == 4:
                if state and self._should_include_field(activity_type, "state", app_id):
                    return state
                return "自定义状态"

            elif activity_type == 5:
                competing_info = f"竞争 {activity_name}"
                if details and self._should_include_field(
                    activity_type, "details", app_id
                ):
                    competing_info += f" ({details})"
                return ("开始", competing_info)

            else:
                return ("开始", f"捣鼓 {activity_name}")

        except Exception as e:
            logger.error(f"格式化单个活动失败: {e}")
            return None
