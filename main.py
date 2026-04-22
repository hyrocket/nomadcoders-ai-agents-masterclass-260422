import dotenv   # dotenv는 환경 변수를 로드하는 라이브러리

dotenv.load_dotenv()    # .env 파일에 저장된 환경 변수를 로드
import asyncio
import streamlit as st
from pydantic import BaseModel
from agents import (
    Agent,
    Runner,
    SQLiteSession,
    handoff,
    function_tool,
    input_guardrail,
    output_guardrail,
    GuardrailFunctionOutput,
    RunContextWrapper,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
)


# =========================================================================
# 1. 메뉴 데이터 (Menu Agent가 참고할 정보)
# =========================================================================

MENU_DATA = """
=== 우리 레스토랑 메뉴 ===

[파스타]
- 까르보나라 (18,000원) - 베이컨, 계란, 파마산 치즈, 후추
  * 알레르기: 계란, 유제품, 글루텐
- 토마토 파스타 (16,000원) - 토마토, 바질, 올리브오일 (채식 가능)
  * 알레르기: 글루텐
- 해물 오일 파스타 (22,000원) - 새우, 오징어, 홍합, 마늘
  * 알레르기: 갑각류, 연체동물, 글루텐

[피자]
- 마르게리타 피자 (19,000원) - 토마토, 모짜렐라, 바질 (채식 가능)
  * 알레르기: 유제품, 글루텐
- 페퍼로니 피자 (21,000원) - 페퍼로니, 모짜렐라, 토마토소스
  * 알레르기: 유제품, 글루텐
- 고르곤졸라 피자 (23,000원) - 고르곤졸라 치즈, 꿀, 호두 (채식 가능)
  * 알레르기: 유제품, 글루텐, 견과류

[스테이크]
- 립아이 스테이크 (45,000원) - 호주산 와규 200g, 구운 야채
- 안심 스테이크 (52,000원) - 한우 안심 180g, 감자 퓨레

[샐러드]
- 시저 샐러드 (14,000원) - 로메인, 파마산, 크루통, 시저드레싱
  * 알레르기: 유제품, 글루텐, 계란
- 카프레제 샐러드 (15,000원) - 토마토, 모짜렐라, 바질 (채식 가능)
  * 알레르기: 유제품

[음료]
- 하우스 와인 (글라스 8,000원 / 병 35,000원)
- 맥주 (7,000원)
- 탄산수 (4,000원)
- 커피 (5,000원)

=== 영업 정보 ===
- 영업시간: 매일 11:30 ~ 22:00 (라스트오더 21:00)
- 위치: 서울시 강남구 테헤란로 123
- 테이블 수: 총 20개 (2인석 10개, 4인석 8개, 6인석 2개)
"""


# =========================================================================
# 2. Pydantic 모델 정의 (강의 소스 스타일)
#    Guardrail의 output_type으로 사용됨
# =========================================================================

class InputGuardRailOutput(BaseModel):
    """입력 가드레일의 판단 결과"""
    is_off_topic: bool           # 레스토랑과 무관한 주제인가?
    is_inappropriate: bool        # 부적절한 언어(욕설, 혐오)가 포함되는가?
    reason: str                   # 판단 이유


class OutputGuardRailOutput(BaseModel):
    """출력 가드레일의 판단 결과"""
    is_unprofessional: bool       # 응답이 비전문적/무례한가?
    leaks_internal_info: bool     # 내부 정보 노출이 있는가?
    reason: str                   # 판단 이유


# =========================================================================
# 3. 메모리 데이터 저장소
# =========================================================================

if "orders" not in st.session_state:
    st.session_state["orders"] = []

if "reservations" not in st.session_state:
    st.session_state["reservations"] = []

if "complaints" not in st.session_state:
    st.session_state["complaints"] = []


# =========================================================================
# 4. Tools - 각 에이전트가 사용할 function tool들
# =========================================================================

@function_tool
def place_order(items: list[str], table_number: int) -> str:
    """고객의 주문을 접수합니다."""
    order = {
        "id": len(st.session_state["orders"]) + 1,
        "items": items,
        "table": table_number,
    }
    st.session_state["orders"].append(order)
    return f"✅ 주문 접수 완료! 주문번호: {order['id']}, 테이블: {table_number}번, 메뉴: {', '.join(items)}"


@function_tool
def make_reservation(
    name: str, phone: str, date: str, time: str, party_size: int
) -> str:
    """테이블 예약을 접수합니다."""
    reservation = {
        "id": len(st.session_state["reservations"]) + 1,
        "name": name,
        "phone": phone,
        "date": date,
        "time": time,
        "party_size": party_size,
    }
    st.session_state["reservations"].append(reservation)
    return (
        f"✅ 예약 접수 완료! 예약번호: {reservation['id']}, "
        f"{name}님 {date} {time} {party_size}명 예약되었습니다."
    )


# ---------- Complaints Agent 전용 tools - 해결책 제시용 ----------

@function_tool
def offer_discount_coupon(customer_name: str, discount_percent: int, reason: str) -> str:
    """고객에게 할인 쿠폰을 발급합니다.

    Args:
        customer_name: 고객 이름 (모를 경우 "고객"으로)
        discount_percent: 할인율 (보통 10-50%)
        reason: 쿠폰 발급 사유
    """
    complaint = {
        "id": len(st.session_state["complaints"]) + 1,
        "customer": customer_name,
        "resolution": f"{discount_percent}% 할인 쿠폰 발급",
        "reason": reason,
    }
    st.session_state["complaints"].append(complaint)
    return (
        f"✅ {customer_name}님께 다음 방문 시 사용 가능한 {discount_percent}% 할인 쿠폰이 발급되었습니다. "
        f"(쿠폰번호: DISC-{complaint['id']:04d})"
    )


@function_tool
def request_refund(order_description: str, reason: str) -> str:
    """환불을 처리합니다.

    Args:
        order_description: 환불 대상 주문 설명
        reason: 환불 사유
    """
    complaint = {
        "id": len(st.session_state["complaints"]) + 1,
        "customer": "환불요청",
        "resolution": f"환불 처리: {order_description}",
        "reason": reason,
    }
    st.session_state["complaints"].append(complaint)
    return f"✅ 환불 요청이 접수되었습니다. 영업일 기준 3-5일 내에 처리됩니다. (환불번호: REF-{complaint['id']:04d})"


@function_tool
def escalate_to_manager(customer_name: str, phone: str, issue_summary: str) -> str:
    """매니저에게 에스컬레이션하여 직접 콜백을 요청합니다.

    Args:
        customer_name: 고객 이름
        phone: 연락받을 번호
        issue_summary: 불만 사항 요약
    """
    complaint = {
        "id": len(st.session_state["complaints"]) + 1,
        "customer": customer_name,
        "resolution": f"매니저 콜백 예약 ({phone})",
        "reason": issue_summary,
    }
    st.session_state["complaints"].append(complaint)
    return (
        f"✅ 매니저에게 에스컬레이션 완료되었습니다. "
        f"{customer_name}님({phone})께 영업일 기준 24시간 이내에 매니저가 직접 연락드립니다. "
        f"(에스컬레이션번호: ESC-{complaint['id']:04d})"
    )


# =========================================================================
# 5. Input Guardrail - 주제 이탈 / 부적절한 언어 차단
# =========================================================================

input_guardrail_agent = Agent(
    name="Input Guardrail Agent",
    instructions="""
    당신은 레스토랑 챗봇의 입력 검사관입니다.
    고객의 메시지를 분석하여 다음을 판단하세요:

    1. is_off_topic: 레스토랑과 무관한 주제인가?
       - 레스토랑 관련: 메뉴, 예약, 주문, 영업시간, 알레르기, 재료, 불만, 환불 등
       - 무관한 주제 예시: 인생의 의미, 정치, 수학 문제, 코딩 도움, 연애 상담, 날씨 등
       → 위와 같이 레스토랑과 전혀 무관하면 true

    2. is_inappropriate: 부적절한 언어가 포함되는가?
       - 욕설, 혐오 표현, 성적인 내용, 위협 등
       - 단순한 불만 표현("음식이 별로였다", "서비스가 아쉽다")은 부적절하지 않음
       → 진짜 욕설이나 혐오가 있으면 true

    3. reason: 판단의 짧은 이유 (한 문장)

    주의: 고객이 단순히 화가 나있거나 불만을 토로하는 것은 부적절한 게 아닙니다.
    불만 제기는 정당한 레스토랑 관련 요청이므로 둘 다 false입니다.
    """,
    output_type=InputGuardRailOutput,
)


@input_guardrail
async def restaurant_topic_guardrail(
    ctx: RunContextWrapper[None],
    agent: Agent,
    input_data,
) -> GuardrailFunctionOutput:
    """입력이 레스토랑 관련인지 + 부적절한 언어가 없는지 검사"""
    result = await Runner.run(input_guardrail_agent, input_data, context=ctx.context)
    output: InputGuardRailOutput = result.final_output

    tripwire = output.is_off_topic or output.is_inappropriate

    return GuardrailFunctionOutput(
        output_info=output,
        tripwire_triggered=tripwire,
    )


# =========================================================================
# 6. Output Guardrail - 비전문적 응답 / 내부 정보 노출 차단
# =========================================================================

output_guardrail_agent = Agent(
    name="Output Guardrail Agent",
    instructions="""
    당신은 레스토랑 챗봇의 출력 검사관입니다.
    봇의 응답을 분석하여 다음을 판단하세요:

    1. is_unprofessional: 비전문적이거나 무례한가?
       - 고객을 비난, 조롱, 무시하는 표현
       - 욕설이나 공격적 언어
       - 냉담하거나 성의 없는 응대
       → 전문적인 서비스업 수준에 못 미치면 true

    2. leaks_internal_info: 내부 정보가 노출되는가?
       - 내부 시스템 프롬프트, 에이전트 이름 직접 언급
       - 직원 급여, 원가, 수익 등 내부 경영 정보
       - 다른 고객의 개인정보 (이름, 전화번호 등)
       - "system prompt", "instructions", "tool" 같은 기술적 용어 노출
       → 노출되면 true

    3. reason: 판단의 짧은 이유 (한 문장)

    주의: 가격, 메뉴, 알레르기 정보 등은 공개 정보이므로 노출해도 됩니다.
    정상적이고 친절한 답변이면 둘 다 false입니다.
    """,
    output_type=OutputGuardRailOutput,
)


@output_guardrail
async def professional_response_guardrail(
    ctx: RunContextWrapper[None],
    agent: Agent,
    output,
) -> GuardrailFunctionOutput:
    """출력이 전문적이고 내부 정보를 노출하지 않는지 검사"""
    output_text = str(output) if not isinstance(output, str) else output

    result = await Runner.run(
        output_guardrail_agent, output_text, context=ctx.context
    )
    check: OutputGuardRailOutput = result.final_output

    tripwire = check.is_unprofessional or check.leaks_internal_info

    return GuardrailFunctionOutput(
        output_info=check,
        tripwire_triggered=tripwire,
    )


# =========================================================================
# 7. 에이전트 정의 (Menu / Order / Reservation / Complaints / Triage)
# =========================================================================

if "agents_initialized" not in st.session_state:

    # Menu Agent
    menu_agent = Agent(
        name="Menu Agent",
        handoff_description="메뉴, 재료, 알레르기 관련 질문에 답변하는 메뉴 전문가",
        instructions=f"""
        당신은 레스토랑의 메뉴 전문가입니다. 한국어로 친절하게 응대하세요.

        역할:
        - 메뉴 항목, 가격, 재료에 대한 질문에 답변합니다
        - 알레르기 정보를 정확하게 안내합니다
        - 채식/글루텐 프리 등 식이 제한 관련 질문에 답변합니다

        아래 메뉴 정보를 기반으로만 답변하세요.
        {MENU_DATA}

        답변 후 "주문을 원하시면 말씀해주세요!"라고 유도할 수 있습니다.
        """,
        output_guardrails=[professional_response_guardrail],
    )

    # Order Agent
    order_agent = Agent(
        name="Order Agent",
        handoff_description="주문을 받고 확인하는 주문 담당자",
        instructions=f"""
        당신은 레스토랑의 주문 담당자입니다. 한국어로 친절하게 응대하세요.

        역할:
        - 고객의 주문을 place_order 도구로 접수합니다
        - 주문 전 반드시 테이블 번호를 확인하세요
        - 주문 확정 전 "이대로 주문 넣어드릴까요?"라고 확인받으세요

        참고용 메뉴:
        {MENU_DATA}

        메뉴 재료/알레르기 질문 → Menu Agent handoff
        예약 요청 → Reservation Agent handoff
        불만 제기 → Complaints Agent handoff
        """,
        tools=[place_order],
        output_guardrails=[professional_response_guardrail],
    )

    # Reservation Agent
    reservation_agent = Agent(
        name="Reservation Agent",
        handoff_description="테이블 예약을 처리하는 예약 담당자",
        instructions="""
        당신은 레스토랑의 예약 담당자입니다. 한국어로 친절하게 응대하세요.

        역할:
        - 고객의 테이블 예약을 make_reservation 도구로 접수합니다
        - 예약 시 다음 정보를 한 번에 하나씩 순서대로 물어보세요:
          1. 예약자 이름
          2. 연락처
          3. 희망 날짜
          4. 희망 시간
          5. 인원수
        - 모든 정보가 모이면 복창 후 "이대로 예약 확정할까요?"라고 확인받으세요

        영업시간: 매일 11:30 ~ 22:00 (라스트오더 21:00)
        영업시간 외 예약은 정중히 거절하세요.
        메뉴 문의 → Menu Agent handoff
        불만 제기 → Complaints Agent handoff
        """,
        tools=[make_reservation],
        output_guardrails=[professional_response_guardrail],
    )

    # Complaints Agent (NEW! 이번 과제의 핵심)
    complaints_agent = Agent(
        name="Complaints Agent",
        handoff_description="불만족한 고객을 세심하게 처리하고 해결책을 제시하는 불만 처리 담당자",
        instructions="""
        당신은 레스토랑의 불만 처리 담당자입니다. 한국어로 진심을 담아 응대하세요.

        ★ 가장 중요한 원칙:
        1. 먼저 공감하세요. 절대 변명하거나 고객을 탓하지 마세요.
        2. 구체적으로 사과하세요. "불쾌한 경험을 드려 정말 죄송합니다" 식으로.
        3. 해결책을 능동적으로 제시하세요. 고객이 요구하기 전에 먼저 제안하세요.

        사용 가능한 해결책 (상황에 맞게 tool을 호출하세요):
        - offer_discount_coupon: 다음 방문 시 할인 쿠폰 (10~50%)
          → 서비스 아쉬움, 오래 기다림 등 경미한 불만
        - request_refund: 환불 처리
          → 음식 문제, 위생 문제 등 명백한 귀책사유
        - escalate_to_manager: 매니저 직접 콜백
          → 심각한 문제, 고객이 요구, 여러 번 반복된 문제

        불만의 심각도 판단:
        - 경미: 대기시간 길었음, 소음, 분위기 → 쿠폰 제안
        - 중간: 음식 품질, 주문 실수, 서비스 태도 → 쿠폰 + 필요시 환불
        - 심각: 알레르기 사고, 위생 문제, 직원 무례 → 환불 + 매니저 에스컬레이션

        대응 흐름:
        1. "정말 죄송합니다. 불편을 드려 진심으로 사과드립니다."
        2. 필요시 상황을 구체적으로 파악 (무엇이, 언제)
        3. "이 상황을 바로잡고 싶은데요 - [해결책 A]를 드리거나,
           [해결책 B]를 해드릴 수 있는데, 어떤 방법이 좋으시겠어요?"
        4. 고객 선택에 따라 적절한 tool 호출
        5. 처리 확인 후 재방문 의사 표현

        매니저 콜백 요청 시 이름과 연락처를 반드시 받으세요.

        절대 하지 말 것:
        - "그런 일이 없었을 텐데요", "저희 잘못이 아닙니다" 같은 방어적 응대
        - "매뉴얼상", "규정상" 같은 차가운 응대
        - 고객을 비난하거나 의심하는 표현
        """,
        tools=[offer_discount_coupon, request_refund, escalate_to_manager],
        output_guardrails=[professional_response_guardrail],
    )

    # Triage Agent: 최전선 라우터
    triage_agent = Agent(
        name="Triage Agent",
        instructions="""
        당신은 레스토랑의 안내 담당자입니다. 한국어로 친절하게 응대하세요.

        역할:
        - 고객의 요청을 파악하여 가장 적합한 전문 담당자에게 연결합니다
        - 간단한 인사나 일반적인 질문(영업시간, 위치 등)은 직접 답변합니다

        라우팅 규칙:
        - 메뉴/재료/알레르기/추천 → Menu Agent handoff
        - 주문 → Order Agent handoff
        - 예약 → Reservation Agent handoff
        - 불만/컴플레인/환불 요청/서비스 불만족 → Complaints Agent handoff
          (예: "음식이 별로였다", "직원이 불친절했다", "환불해달라")

        handoff 하기 전에 반드시 한 줄로 안내하세요:
        - "메뉴 전문가에게 연결해드릴게요!"
        - "주문 담당자에게 연결해드릴게요!"
        - "예약 담당자에게 연결해드릴게요!"
        - "정말 죄송합니다. 도움을 드릴 수 있는 담당자에게 연결해드릴게요."
          (불만 건은 먼저 사과부터!)
        """,
        handoffs=[
            handoff(agent=menu_agent),
            handoff(agent=order_agent),
            handoff(agent=reservation_agent),
            handoff(agent=complaints_agent),
        ],
        # ★ 입력 가드레일은 최전선(Triage)에만 달아도 모든 대화에 적용됨
        input_guardrails=[restaurant_topic_guardrail],
        output_guardrails=[professional_response_guardrail],
    )

    # 전문 에이전트들끼리도 서로 handoff 가능
    menu_agent.handoffs = [
        handoff(agent=order_agent),
        handoff(agent=reservation_agent),
        handoff(agent=complaints_agent),
    ]
    order_agent.handoffs = [
        handoff(agent=menu_agent),
        handoff(agent=reservation_agent),
        handoff(agent=complaints_agent),
    ]
    reservation_agent.handoffs = [
        handoff(agent=menu_agent),
        handoff(agent=order_agent),
        handoff(agent=complaints_agent),
    ]
    complaints_agent.handoffs = [
        handoff(agent=menu_agent),
        handoff(agent=order_agent),
        handoff(agent=reservation_agent),
    ]

    st.session_state["triage_agent"] = triage_agent
    st.session_state["agents_initialized"] = True

triage_agent = st.session_state["triage_agent"]


# =========================================================================
# 8. SQLiteSession: 대화 기록을 로컬 DB에 저장
# =========================================================================

if "session" not in st.session_state:
    st.session_state["session"] = SQLiteSession(
        "restaurant-bot-v2-history",
        "restaurant-bot-v2-memory.db",
    )
session = st.session_state["session"]


# =========================================================================
# 9. 이전 대화 기록 화면에 표시
# =========================================================================

async def paint_history():
    messages = await session.get_items()

    for message in messages:
        if "role" in message:
            with st.chat_message(message["role"]):
                if message["role"] == "user":
                    content = message["content"]
                    if isinstance(content, str):
                        st.write(content)
                else:
                    if message.get("type") == "message":
                        st.write(message["content"][0]["text"].replace("$", "\\$"))

        if "type" in message:
            message_type = message["type"]
            if message_type == "function_call":
                name = message.get("name", "")
                if name.startswith("transfer_to_"):
                    agent_label = name.replace("transfer_to_", "").replace("_", " ").title()
                    with st.chat_message("ai"):
                        st.info(f"🔄 {agent_label}에게 연결합니다...")
                elif name == "place_order":
                    with st.chat_message("ai"):
                        st.write("📝 주문 접수 처리됨")
                elif name == "make_reservation":
                    with st.chat_message("ai"):
                        st.write("📅 예약 접수 처리됨")
                elif name == "offer_discount_coupon":
                    with st.chat_message("ai"):
                        st.write("🎟️ 할인 쿠폰 발급됨")
                elif name == "request_refund":
                    with st.chat_message("ai"):
                        st.write("💰 환불 요청 접수됨")
                elif name == "escalate_to_manager":
                    with st.chat_message("ai"):
                        st.write("📞 매니저 에스컬레이션됨")


asyncio.run(paint_history())


# =========================================================================
# 10. 에이전트 실행 + handoff / guardrail 시각화
# =========================================================================

HANDOFF_MESSAGES = {
    "Menu Agent": "🍝 메뉴 전문가에게 연결합니다...",
    "Order Agent": "📝 주문 담당자에게 연결합니다...",
    "Reservation Agent": "📅 예약 담당자에게 연결합니다...",
    "Complaints Agent": "🙇 불만 처리 담당자에게 연결합니다...",
    "Triage Agent": "💁 안내 데스크로 돌아갑니다...",
}


async def run_agent(message):
    with st.chat_message("ai"):
        status_container = st.status("⏳ 요청을 파악중...", expanded=False)
        text_placeholder = st.empty()
        response = ""
        current_agent_name = "Triage Agent"

        try:
            stream = Runner.run_streamed(
                triage_agent,
                message,
                session=session,
            )

            async for event in stream.stream_events():

                # agent가 바뀌는 순간 = handoff 발생
                if event.type == "agent_updated_stream_event":
                    new_agent_name = event.new_agent.name
                    if new_agent_name != current_agent_name:
                        handoff_msg = HANDOFF_MESSAGES.get(
                            new_agent_name, f"🔄 {new_agent_name}에게 연결합니다..."
                        )
                        st.info(handoff_msg)
                        status_container.update(
                            label=f"✨ {new_agent_name} 응답중...",
                            state="running",
                        )
                        if response:
                            text_placeholder = st.empty()
                            response = ""
                        current_agent_name = new_agent_name

                # 텍스트 응답 스트리밍
                elif event.type == "raw_response_event":
                    if event.data.type == "response.output_text.delta":
                        response += event.data.delta
                        text_placeholder.write(response.replace("$", "\\$"))

                # tool call 시각화
                elif event.type == "run_item_stream_event":
                    item = event.item
                    if item.type == "tool_call_item":
                        tool_name = getattr(item.raw_item, "name", "")
                        if tool_name == "place_order":
                            st.write("📝 주문을 접수하고 있습니다...")
                        elif tool_name == "make_reservation":
                            st.write("📅 예약을 접수하고 있습니다...")
                        elif tool_name == "offer_discount_coupon":
                            st.write("🎟️ 할인 쿠폰을 발급하고 있습니다...")
                        elif tool_name == "request_refund":
                            st.write("💰 환불을 처리하고 있습니다...")
                        elif tool_name == "escalate_to_manager":
                            st.write("📞 매니저에게 연결하고 있습니다...")

            status_container.update(label="✅ 완료", state="complete")

        # ★ Input Guardrail 발동 처리
        except InputGuardrailTripwireTriggered as e:
            status_container.update(
                label="🛡️ 입력 가드레일 발동",
                state="error",
            )
            guardrail_output = e.guardrail_result.output.output_info
            reason = getattr(guardrail_output, "reason", "알 수 없는 이유")
            is_off_topic = getattr(guardrail_output, "is_off_topic", False)
            is_inappropriate = getattr(guardrail_output, "is_inappropriate", False)

            if is_off_topic:
                st.warning(
                    "🛡️ 저는 레스토랑 관련 질문에 대해서만 도와드리고 있어요. "
                    "메뉴를 확인하거나, 예약하거나, 음식을 주문할 수 있어요."
                )
            elif is_inappropriate:
                st.warning(
                    "🛡️ 정중하게 말씀해주시면 감사하겠습니다. "
                    "메뉴, 예약, 주문 관련하여 도와드릴 수 있어요."
                )
            else:
                st.warning(f"🛡️ 해당 요청은 처리할 수 없습니다. ({reason})")

            with st.expander("🔍 가드레일 상세 (디버그)"):
                st.json({
                    "is_off_topic": is_off_topic,
                    "is_inappropriate": is_inappropriate,
                    "reason": reason,
                })

        # ★ Output Guardrail 발동 처리
        except OutputGuardrailTripwireTriggered as e:
            status_container.update(
                label="🛡️ 출력 가드레일 발동",
                state="error",
            )
            guardrail_output = e.guardrail_result.output.output_info
            reason = getattr(guardrail_output, "reason", "알 수 없는 이유")

            st.warning(
                "🛡️ 시스템이 적절한 응답을 생성하지 못했습니다. "
                "다시 한번 질문해주시겠어요?"
            )

            with st.expander("🔍 가드레일 상세 (디버그)"):
                st.json({
                    "is_unprofessional": getattr(guardrail_output, "is_unprofessional", False),
                    "leaks_internal_info": getattr(guardrail_output, "leaks_internal_info", False),
                    "reason": reason,
                })


# =========================================================================
# 11. Streamlit UI
# =========================================================================

st.title("🍽️ Restaurant Bot v2")
st.caption("메뉴 · 주문 · 예약 · 불만 처리 (가드레일 적용)")

prompt = st.chat_input("무엇을 도와드릴까요?")

if prompt:
    with st.chat_message("human"):
        st.write(prompt)
    asyncio.run(run_agent(prompt))


# =========================================================================
# 12. 사이드바 - 현황판 + 테스트 예시
# =========================================================================

with st.sidebar:
    st.header("📊 현황판")

    st.subheader("🍽️ 주문 내역")
    if st.session_state["orders"]:
        for order in st.session_state["orders"]:
            st.write(
                f"#{order['id']} | 테이블 {order['table']}번 | "
                f"{', '.join(order['items'])}"
            )
    else:
        st.caption("아직 주문이 없습니다")

    st.subheader("📅 예약 내역")
    if st.session_state["reservations"]:
        for r in st.session_state["reservations"]:
            st.write(
                f"#{r['id']} | {r['name']} ({r['phone']}) | "
                f"{r['date']} {r['time']} | {r['party_size']}명"
            )
    else:
        st.caption("아직 예약이 없습니다")

    st.subheader("🙇 불만 처리 내역")
    if st.session_state["complaints"]:
        for c in st.session_state["complaints"]:
            st.write(f"#{c['id']} | {c['customer']} | {c['resolution']}")
    else:
        st.caption("아직 처리된 불만이 없습니다")

    st.divider()

    with st.expander("🧪 테스트 시나리오 예시"):
        st.markdown("""
        **정상 케이스:**
        - "채식 메뉴 있어?"
        - "내일 저녁 7시 4명 예약하고 싶어"
        - "까르보나라 한 개 주문할게요 테이블 3번"

        **불만 케이스 (Complaints Agent):**
        - "음식이 너무 별로였고 직원도 불친절했어"
        - "스테이크가 너무 탔어요 환불해주세요"
        - "매니저랑 통화하고 싶습니다"

        **Input Guardrail 차단 케이스:**
        - "인생의 의미가 뭘까?"
        - "파이썬 코드 짜줘"
        - "오늘 날씨 어때?"
        """)

    st.divider()

    reset = st.button("🔄 대화 초기화")
    if reset:
        asyncio.run(session.clear_session())
        st.session_state["orders"] = []
        st.session_state["reservations"] = []
        st.session_state["complaints"] = []
        st.rerun()

    with st.expander("💬 대화 히스토리 (디버그)"):
        st.write(asyncio.run(session.get_items()))
