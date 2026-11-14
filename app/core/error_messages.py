"""受信上屏错误目录；异常文本只能作为精确匹配键，不能直接输出。"""

from enum import StrEnum


class ErrorMessage(StrEnum):
    INTERNAL = "服务暂时不可用，请稍后重试。"
    CLIENT = "请求未能完成，暂时无法确认原因；请联系维护人员并提供问题编号。"
    NOT_FOUND = "资源不存在或无法访问，请返回列表重新选择。"
    PROFILE_NOT_FOUND = "简历不存在或无法访问，请返回简历列表重新选择。"
    JOB_NOT_FOUND = "岗位不存在或无法访问，请返回岗位列表重新选择。"
    METHOD_NOT_ALLOWED = "此操作暂不支持，请返回页面使用可用按钮。"
    UNAUTHORIZED = "登录已失效，请重新登录。"
    FORBIDDEN = "您没有执行此操作的权限，请联系维护人员确认访问权限。"
    REFRESH_REJECTED = "登录已过期，请重新登录。"
    USERNAME_EXISTS = "用户名已被注册，请更换用户名或直接登录。"
    EMAIL_EXISTS = "邮箱已被注册，请更换邮箱或使用原账号登录。"
    PHONE_EXISTS = "这个手机号已被注册，请更换手机号或直接用手机号登录。"
    INVALID_CREDENTIALS = "用户名或密码错误，请检查输入后重新登录。"
    # ---- 批次 B6 收口（ADR-025 §2.4）：改密。三句都要求用户「做什么」，不描述内部状态。 ----
    PASSWORD_CURRENT_WRONG = "当前密码不正确，请重新输入后再改。"
    PASSWORD_UNCHANGED = "新密码与当前密码相同，请换一个没在用的密码。"
    PROFILE_TITLE_CONFLICT = "该简历名称已存在，请换一个名称。"
    PROFILE_LIMIT = "最多只能保存 20 份简历，请先删除不再需要的简历。"
    PROFILE_NOT_TRASHED = "只能彻底删除回收站中的简历，请先删除再彻底删除。"
    PROFILE_INCOMPLETE = "请先完善个人档案（基本信息、技能、经历）后再生成简历。"
    PROFILE_REQUIRED = "还没有可用的档案：请先完善并保存个人档案。"
    PRIVACY_PROFILE_REQUIRED = "还没有可检查的简历：请先完善并保存个人档案。"
    RESUME_NOT_GENERATED = "还没有生成过简历，请先生成一份。"
    RESUME_POINTER_BROKEN = "当前简历暂时无法读取，请重新生成一份简历。"
    # ---- 批次 B8 剩余（ADR-026）：版本对比与按岗位定制。 ----
    # 版本 id 不存在 / 不属于自己 / 与另一版不同档案：三句合一，不区分原因。
    RESUME_VERSION_NOT_FOUND = "这一版简历不存在或无法访问，请返回版本列表重新选择。"
    LAYOUT_VERSION = "你的排版设置是旧版本，请在排版面板重新保存一次再生成"
    RATE_LIMITED = "请求过于频繁，请稍后再试。"
    # ---- 批次 B5（ADR-021）：组织与角色。文案集中在目录里，路由只引用枚举文案。 ----
    # 注：`ORGANIZATION_REQUIRED` / `ORGANIZATION_CODE_INVALID` 已随注册入口收口删除
    # （ADR-025 §1.1）——注册不再接受 `organization_code`，这两句没有调用点了，
    # 留着只会让人以为还有一条「带码注册」的路。
    ORGANIZATION_FORBIDDEN = "这个组织不属于你的账号。"
    ORGANIZATION_MEMBER_NOT_FOUND = "这个用户不在本组织的成员名单里。"
    SELF_ROLE_CHANGE = "不能修改自己的角色，请让另一位管理员操作。"
    ROLE_NOT_ASSIGNABLE = "这个角色不能在这里授予。"
    ORGANIZATION_ACTION_FAILED = "组织操作没有完成，请稍后重试。"
    # ---- 批次 B6（ADR-023）：组织邀请。四种不可用情形（不存在/撤销/过期/用尽）
    # 对外同一句，不区分细节，避免这个端点变成「猜邀请码是否存在」的探针。 ----
    INVITATION_INVALID = "邀请码无效或已失效，请向学校管理员重新获取。"
    INVITATION_ALREADY_JOINED = "你的账号已加入组织，无需再使用邀请码。"
    INVITATION_NO_ORGANIZATION = "你还不属于任何组织，暂时不能发出邀请。"
    # ---- 批次 B6 另一半（ADR-024）：岗位审核流。 ----
    # ① 第 18 轮裁定「发布岗位必须依托组织」的对外文案：说清下一步动作，不说「无权限」。
    JOB_ORGANIZATION_REQUIRED = "发布岗位需要先绑定组织，请用邀请码加入学校组织后再提交审核。"
    # ② 状态冲突：审核人/创建者本来就看得见这条岗位，藏着只会让人反复点。
    JOB_STATE_CONFLICT = "这条岗位的状态已经变了，请刷新页面后重试。"
    # ③ 动作层的过期拦截（裁定 ③）：给的是能解决的那一步，不是「已过期」三个字。
    JOB_EXPIRED = "岗位已过截止时间，请先更新截止日期，再提交或审核。"
    # ④ 不能自审：教师角色可由邀请码获得，自审等于没有审（ADR-024 §2.2）。
    JOB_SELF_REVIEW = "不能审核自己提交的岗位，请让同组织的其他审核人处理。"
    # ---- 批次 B11a（ADR-022）：面试会话。 ----
    INTERVIEW_NOT_FOUND = "面试会话不存在或无法访问，请返回面试列表重新选择。"
    INTERVIEW_ENDED = "这场面试已经结束，请新建一场继续练习。"
    # ---- 批次 B9（ADR-028）：标准化题库测评与画像。 ----
    # ① 会话不存在 / 不属于自己：与岗位、简历、面试同一句口径，不区分原因。
    ASSESSMENT_NOT_FOUND = "这次测评不存在或无法访问，请返回测评列表重新选择。"
    # ② 状态冲突（重复提交 / 提交后改答案 / 放弃已提交的）：说清「为什么不能再改」，
    #    并给下一步动作。只说「状态不允许」等于让用户自己猜。
    ASSESSMENT_STATE_CONFLICT = "这次测评已经提交或已放弃，不能再修改；想再测一次请新建一场测评。"
    # ③ 选项不合法：用户选的 key 不属于那道题（贴值 / 旧页面 / 题目被停用）。
    ASSESSMENT_ANSWER_INVALID = "这题的选项已无效，请刷新页面后重新作答。"
    # ④ 题库为空：部署数据问题，但也不能回 500 —— 用户看到的是「暂时不能测」。
    ASSESSMENT_BANK_EMPTY = "测评题库暂时不可用，请稍后再试或联系管理员。"
    # ---- 批次 B10a / B10b（ADR-029）：教师端工具。 ----
    # ① 动作层组织闸（第 18 轮裁定 ① / 第 19 轮硬约束）：一句话说清「下一步做什么」，
    #    不写「无权限」「禁止访问」这类让人以为账号坏了的话。看板、下载、导出共用这一句。
    ORGANIZATION_ACTION_REQUIRED = "这个操作需要先绑定组织，请用邀请码加入学校组织后再试。"
    # ② 批量下载时一个可下载项都没剩下：不是系统坏，是这批 id 都不属于本组织。
    NO_DOWNLOADABLE_RESUME = "这批简历没有可下载的版本，请重新选择后重试。"
    # ③ 导出找不到简历 / 不属于本组织：与岗位、面试同一条 404 口径。
    RESUME_EXPORT_NOT_FOUND = "这份简历不存在或无法访问，请返回简历列表重新选择。"
    # ④ 宿主机没有无头浏览器：给一条真能走的路（浏览器打印），绝不回一个假 PDF。
    PDF_RENDERER_UNAVAILABLE = (
        "暂时无法生成 PDF，请先用浏览器的打印功能保存为 PDF，或联系管理员配置导出服务。"
    )
    # ---- 批次 B7 证据链：教师确认技能（`ADR-027` §5 第 3 条）。 ----
    # ① 档案不在本组织 / 已删除：与「这份简历不存在」同一条 404 口径，
    #    不区分两种原因（区分就等于告诉外人「这个 id 存在，只是不归你」）。
    SKILL_CONFIRM_PROFILE_NOT_FOUND = "这个学生的档案不在你的组织里，或已被删除。"
    # ② 技能名为空 / 超长：确认动作必须点名一个技能，否则等于什么都没说。
    SKILL_CONFIRM_NAME_INVALID = "请填写要确认的技能名称，长度不超过 100 个字符。"
    # ui-copy:allow 此处名称用于定位用户必须配置的凭据，不展示凭据内容。
    AI_KEY_MISSING = "请先在「AI 设置」中配置 DeepSeek API Key。"
    # ui-copy:allow 此处名称用于定位用户必须检查的凭据，不展示凭据内容。
    AI_KEY_INVALID = "您的 DeepSeek API Key 无效或已欠费，请前往「AI 设置」检查后重试。"
    AI_UNAVAILABLE = "AI 服务当前不可用，本次请求未被保存，您的资料不受影响。请稍后重试。"
    EXTRACT_TRUNCATED = (
        "AI 输出的技能条目过多，结果未能完整返回。"
        "请重试，并只粘贴岗位描述的核心段落，或减少期望的条数。"
    )
    VALIDATION = "填写内容有误，请按字段提示修改后提交。"
    INVALID_FIELD = "{field}填写有误，请检查该项的格式与长度后提交。"
    REQUIRED_FIELD = "{field}未填写，请补充后提交。"
    TEXT_FIELD = "{field}格式不正确，请填写文字后提交。"
    NUMBER_FIELD = "{field}格式不正确，请填写数字后提交。"
    INTEGER_FIELD = "{field}格式不正确，请填写整数后提交。"
    DATE_FIELD = "{field}日期无效，请选择有效日期后提交。"
    CHOICE_FIELD = "{field}选项无效，请从页面提供的选项中重新选择。"
    SHORT_FIELD = "{field}内容过短，请补充至至少 {bound} 个字符后提交。"
    LONG_FIELD = "{field}内容过长，请精简至 {bound} 个字符以内后提交。"
    FEW_ITEMS = "{field}条目不足，请至少填写 {bound} 条后提交。"
    MANY_ITEMS = "{field}条目过多，请减少至 {bound} 条以内后提交。"
    NUMBER_MIN = "{field}数值过小，请填写不小于 {bound} 的数值。"
    NUMBER_MAX = "{field}数值过大，请填写不大于 {bound} 的数值。"
    EXTRA_FIELD = "{field}含不支持的填写项，请刷新页面后重新填写。"
    JSON_INVALID = "提交内容格式损坏，请刷新页面后重新填写并提交。"
    MAX_WORDS = "目标字数须为 40–800 的整数，请修改目标字数后再开始润色。"
    USERNAME_FORMAT = "用户名须为 3–100 个字符且不能含空白，请修改用户名。"
    # ADR-025 §2.3：用户名不许长得像邮箱/手机号。**必须有独立一句**——归到
    # `USERNAME_FORMAT` 会让用户填了 11 位手机号却被告知「长度或空白不对」，
    # 照着改十次也过不去（实测：这正是收口后第一版的行为）。
    USERNAME_NOT_AN_IDENTIFIER = (
        "用户名不能是邮箱或手机号：邮箱请填在「邮箱」一栏，手机号请填在「手机号」一栏。"
    )
    PASSWORD_BYTES = "密码过长，请缩短至 72 字节以内（中文通常每字占 3 字节）。"
    EMAIL_FORMAT = "邮箱格式不正确，请填写如 name@example.com 的地址。"
    CONTACT_FORMAT = "手机号格式不正确，请填写中国大陆 11 位手机号。"
    SKILL_BLANK = "{field}的技能名称为空，请填写技能名称或删除空条目。"
    #: B7（`ADR-027`）：技能的「最近使用」不可能是明天；「测评」来源不是用户能选的。
    #: 两句都必须是**能动手**的话——前一版若只说「格式不正确」，用户不知道该改成什么。
    SKILL_LAST_USED_FUTURE = "技能的「最近使用」不能填未来日期，请选择今天或更早的月份。"
    SKILL_SOURCE_ASSESSMENT = (
        "「已通过测评」这个来源不能手动选择：它由标准化测评提交时自动写入，"
        "现在请选择「自评」或「项目」。"
    )
    SKILL_DUPLICATE = "技能名称重复，请合并同名技能后提交。"
    EXPERIENCE_DATES = "{field}的日期有误，请确保开始日期不晚于今天、结束日期不早于开始日期。"
    EDUCATION_DATES = "{field}的日期有误，请确保入学日期不晚于今天、毕业日期不早于入学日期。"
    SALARY_RANGE = "薪资范围有误，请将最低薪资改为不高于最高薪资的数值。"
    LAYOUT_DUPLICATE = "排版模块重复，请删除重复模块后重新保存排版。"
    TITLE_BLANK = "{field}标题为空，请填写标题后提交。"
    CERTIFICATE_BLANK = "{field}证书名称为空，请填写名称或删除空条目。"
    CERTIFICATE_DUPLICATE = "证书名称重复，请合并同名证书后提交。"
    PORTFOLIO_LABEL = "{field}作品名称为空，请填写名称或删除空条目。"
    PORTFOLIO_URL = "{field}作品链接无效，请填写以 http:// 或 https:// 开头的完整链接。"
    PORTFOLIO_DUPLICATE = "作品链接重复，请删除重复链接后提交。"
    CITY_BLANK = "{field}城市为空，请填写城市或删除空条目。"
    CITY_DUPLICATE = "期望城市重复，请删除重复城市后提交。"


FIELD_LABELS = {
    "body": "填写内容",
    "query": "筛选条件",
    "path": "访问地址",
    "header": "请求信息",
    "field": "填写项",
    "profile": "基本信息",
    "full_name": "姓名",
    "contact": "手机号",
    "username": "用户名",
    "password": "密码",
    "email": "邮箱",
    "phone": "手机号",
    # ADR-025 §2.1 / §2.4：登录标识与改密两格。
    "identifier": "登录账号",
    "current_password": "当前密码",
    "new_password": "新密码",
    "refresh_token": "登录凭据",
    "title": "标题",
    "profile_id": "简历",
    "copy_from_id": "来源简历",
    "is_default": "默认简历",
    "skills": "技能",
    "name": "名称",
    "experiences": "项目经历",
    "description": "描述",
    "responsibility": "个人职责",
    "start_date": "开始日期",
    "end_date": "结束日期",
    "educations": "教育经历",
    "school": "学校",
    "degree": "学历",
    "major": "专业",
    "edu_start": "入学日期",
    "edu_end": "毕业日期",
    "grade": "年级",
    "target_role": "目标岗位",
    "layout_config": "排版设置",
    "sections": "模块",
    "version": "排版版本",
    "id": "编号",
    "paper_size": "纸张",
    "theme": "配色",
    "mode": "润色档位",
    "max_words": "目标字数",
    "company_name": "企业名称",
    "location": "工作地点",
    "employment_type": "工作方式",
    "salary_min": "最低薪资",
    "salary_max": "最高薪资",
    "experience_required": "经验要求",
    "deadline": "截止日期",
    "type": "技能分类",
    "evidence_text": "原文依据",
    "confidence": "把握程度",
    "status": "岗位状态",
    "keyword": "搜索词",
    "include_expired": "过期岗位选项",
    "page": "页码",
    "page_size": "每页条数",
    "order": "排序方向",
    "order_by": "排序方式",
    "certificates": "证书",
    "issuer": "颁发机构",
    "issued_on": "颁发日期",
    "portfolio_links": "作品链接",
    "label": "作品名称",
    "url": "链接",
    "city_preferences": "期望城市",
    "city": "城市",
}

HTTP_DETAIL_ALIASES = {
    "Not Found": ErrorMessage.NOT_FOUND,
    "Method Not Allowed": ErrorMessage.METHOD_NOT_ALLOWED,
    "简历不存在。": ErrorMessage.PROFILE_NOT_FOUND,
    "岗位不存在。": ErrorMessage.JOB_NOT_FOUND,
    "资源不存在。": ErrorMessage.NOT_FOUND,
    "用户名已被注册。": ErrorMessage.USERNAME_EXISTS,
    "邮箱已被注册。": ErrorMessage.EMAIL_EXISTS,
    "用户名或密码错误。": ErrorMessage.INVALID_CREDENTIALS,
    "该简历名称已存在": ErrorMessage.PROFILE_TITLE_CONFLICT,
    "最多只能保存 20 份简历": ErrorMessage.PROFILE_LIMIT,
    "无效或已过期的访问令牌。": ErrorMessage.UNAUTHORIZED,
    "无效或已过期的刷新令牌，请重新登录。": ErrorMessage.REFRESH_REJECTED,
    "当前简历的版本指针已失效，请重新生成一份简历。": ErrorMessage.RESUME_POINTER_BROKEN,
}

VALIDATOR_MESSAGES = {
    "username must contain between 3 and 100 characters": ErrorMessage.USERNAME_FORMAT,
    "username must not contain whitespace": ErrorMessage.USERNAME_FORMAT,
    # ADR-025 §2.3：用户名不许长得像邮箱或手机号（否则登录优先级会截胡别人的标识）。
    # 单列一句而不是并进 `USERNAME_FORMAT`，见该枚举成员的注释。
    "username must not look like an email or a mobile number": (
        ErrorMessage.USERNAME_NOT_AN_IDENTIFIER
    ),
    "password must not exceed 72 bytes when encoded as UTF-8": ErrorMessage.PASSWORD_BYTES,
    "email format is invalid": ErrorMessage.EMAIL_FORMAT,
    "phone must be a mainland China mobile number": ErrorMessage.CONTACT_FORMAT,
    "contact must be a mainland China mobile number": ErrorMessage.CONTACT_FORMAT,
    "skill name must not be blank": ErrorMessage.SKILL_BLANK,
    "last_used_on must not be in the future": ErrorMessage.SKILL_LAST_USED_FUTURE,
    "skill source assessment is written by the server": ErrorMessage.SKILL_SOURCE_ASSESSMENT,
    "skill names must be unique": ErrorMessage.SKILL_DUPLICATE,
    "end_date must be on or after start_date": ErrorMessage.EXPERIENCE_DATES,
    "start_date must not be in the future": ErrorMessage.EXPERIENCE_DATES,
    "edu_end must be on or after edu_start": ErrorMessage.EDUCATION_DATES,
    "edu_start must not be in the future": ErrorMessage.EDUCATION_DATES,
    "salary_min must not be greater than salary_max": ErrorMessage.SALARY_RANGE,
    "layout_config.sections must not contain duplicate ids": ErrorMessage.LAYOUT_DUPLICATE,
    "layout section title must not be blank": ErrorMessage.TITLE_BLANK,
    "title must not be blank": ErrorMessage.TITLE_BLANK,
    "certificate name must not be blank": ErrorMessage.CERTIFICATE_BLANK,
    "certificate names must be unique": ErrorMessage.CERTIFICATE_DUPLICATE,
    "portfolio label must not be blank": ErrorMessage.PORTFOLIO_LABEL,
    "portfolio url must not be blank": ErrorMessage.PORTFOLIO_URL,
    "portfolio url must start with http:// or https://": ErrorMessage.PORTFOLIO_URL,
    "portfolio urls must be unique": ErrorMessage.PORTFOLIO_DUPLICATE,
    "city must not be blank": ErrorMessage.CITY_BLANK,
    "city preferences must be unique": ErrorMessage.CITY_DUPLICATE,
}

VALIDATION_TYPES = {
    "missing": ErrorMessage.REQUIRED_FIELD,
    "string_type": ErrorMessage.TEXT_FIELD,
    "int_type": ErrorMessage.INTEGER_FIELD,
    "int_parsing": ErrorMessage.INTEGER_FIELD,
    "int_from_float": ErrorMessage.INTEGER_FIELD,
    "float_type": ErrorMessage.NUMBER_FIELD,
    "float_parsing": ErrorMessage.NUMBER_FIELD,
    "decimal_parsing": ErrorMessage.NUMBER_FIELD,
    "decimal_type": ErrorMessage.NUMBER_FIELD,
    "date_parsing": ErrorMessage.DATE_FIELD,
    "date_from_datetime_parsing": ErrorMessage.DATE_FIELD,
    "date_type": ErrorMessage.DATE_FIELD,
    "date_from_datetime_inexact": ErrorMessage.DATE_FIELD,
    "literal_error": ErrorMessage.CHOICE_FIELD,
    "enum": ErrorMessage.CHOICE_FIELD,
    "bool_parsing": ErrorMessage.CHOICE_FIELD,
    "bool_type": ErrorMessage.CHOICE_FIELD,
    "extra_forbidden": ErrorMessage.EXTRA_FIELD,
    "json_invalid": ErrorMessage.JSON_INVALID,
    "string_too_short": ErrorMessage.SHORT_FIELD,
    "string_too_long": ErrorMessage.LONG_FIELD,
    "too_short": ErrorMessage.FEW_ITEMS,
    "too_long": ErrorMessage.MANY_ITEMS,
    "greater_than_equal": ErrorMessage.NUMBER_MIN,
    "less_than_equal": ErrorMessage.NUMBER_MAX,
    "value_error": ErrorMessage.INVALID_FIELD,
    "list_type": ErrorMessage.INVALID_FIELD,
    "model_attributes_type": ErrorMessage.INVALID_FIELD,
}


def http_error_message(status_code: int, detail: object) -> ErrorMessage:
    if status_code >= 500 or status_code < 400:
        return ErrorMessage.INTERNAL
    if isinstance(detail, ErrorMessage):
        return detail
    if type(detail) is str:
        if detail in HTTP_DETAIL_ALIASES:
            return HTTP_DETAIL_ALIASES[detail]
        try:
            message = ErrorMessage(detail)
        except ValueError:
            pass
        else:
            if "{" not in message:
                return message
    return {
        401: ErrorMessage.UNAUTHORIZED,
        403: ErrorMessage.FORBIDDEN,
        404: ErrorMessage.NOT_FOUND,
        405: ErrorMessage.METHOD_NOT_ALLOWED,
        429: ErrorMessage.RATE_LIMITED,
    }.get(status_code, ErrorMessage.CLIENT)


#: 模板里 `{bound}` 该填哪个 `ctx` 键。顺序即优先级：同一个错误类型只会有其中一个。
_BOUND_KEYS = ("min_length", "max_length", "ge", "le", "gt", "lt")


def _loc_parts(error: dict[str, object]) -> list[object]:
    """`loc` 是 pydantic 塞进来的任意值：只认序列，别的（缺失 / None / 意外类型）当空。

    这里做一次收窄而不是 `list(error.get("loc") or ())`：后者在 mypy 眼里是
    `list(object)`，没有匹配的重载——而这个函数被每个 422 调用一次，
    真出错时红的是「整个错误模型」，不是一条文案。
    """
    loc = error.get("loc")
    if isinstance(loc, (tuple, list)):
        return list(loc)
    return []


def field_label(loc: tuple[object, ...] | list[object]) -> str:
    """把 pydantic 的 `loc` 翻成一个能读的字段名。

    从**最内层**往外找第一个认识的键：`("body","skills",0,"name")` 要的是「名称」而不是
    「技能」——用户改的是那一行的名称。都不认识时退到最外层，再退到「填写项」。
    """
    parts = [part for part in loc if isinstance(part, str)]
    for part in reversed(parts):
        if part in FIELD_LABELS:
            return FIELD_LABELS[part]
    if parts:
        return FIELD_LABELS.get(parts[-1], FIELD_LABELS["field"])
    return FIELD_LABELS["field"]


def validation_field_message(error: dict[str, object]) -> str:
    """把一条 pydantic 错误变成**用户能照着改**的一句话（守则 C3）。

    两条查表路线，顺序不能反：

    1. **自定义校验器**的原话（`VALIDATOR_MESSAGES`）优先——它知道业务语义
       （「结束日期不早于开始日期」），比按类型猜的通用句准。pydantic 会给它加
       `Value error, ` 前缀，所以先剥掉再查；
    2. 否则按 `type` 走 `VALIDATION_TYPES`（缺字段 / 类型不对 / 超长 / 越界…），
       并把 `ctx` 里的边界值填进 `{bound}`。

    都不命中时落到 `INVALID_FIELD`，**绝不回落到 pydantic 的英文 msg**：那正是 F1c 要堵的洞。
    """
    ctx = error.get("ctx")
    context: dict[str, object] = ctx if isinstance(ctx, dict) else {}
    raw = str(error.get("msg", ""))
    template = VALIDATOR_MESSAGES.get(raw.removeprefix("Value error, ")) or VALIDATION_TYPES.get(
        str(error.get("type", ""))
    )
    if template is None:
        template = ErrorMessage.INVALID_FIELD
    bound = next((context[key] for key in _BOUND_KEYS if key in context), "")
    return template.format(field=field_label(_loc_parts(error)), bound=bound)


def validation_details(errors: list[dict[str, object]]) -> list[dict[str, object]]:
    """给每条字段错误补一句人话，**保留 `loc` 与 `type`**（排障与既有断言都靠它们）。

    只加不减：原始 `msg` 是英文诊断，留在响应里没有意义（用户看不懂，前端也不渲染），
    所以换成本地化后的 `message`，`input` 一并去掉——它可能回显用户输入的长文本。
    """
    details: list[dict[str, object]] = []
    for error in errors:
        details.append(
            {
                "loc": list(_loc_parts(error)),
                "type": error.get("type"),
                "message": validation_field_message(error),
            }
        )
    return details
