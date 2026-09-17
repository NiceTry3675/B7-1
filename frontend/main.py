import gradio as gr
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from frontend.client import BackendClient
from frontend.controller import Controller, Session


class UISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    backend_base_url: str = "http://127.0.0.1:8000"
    backend_timeout_seconds: float = Field(default=160, gt=0)
    gradio_server_name: str = "127.0.0.1"
    gradio_server_port: int = 7860


def build_ui(settings=None, api=None):
    settings = settings or UISettings()
    controller = Controller(
        api or BackendClient(settings.backend_base_url, settings.backend_timeout_seconds)
    )
    with gr.Blocks(title="시대를 넘어, 나누는 대화") as demo:
        state = gr.State(Session())
        gr.Markdown("# 시대를 넘어, 나누는 대화\n위인의 관점에서 오늘의 고민을 함께 생각해 보세요.")
        notice = gr.Textbox(label="안내", interactive=False)
        with gr.Group() as auth:
            email = gr.Textbox(label="이메일", placeholder="student@example.com")
            password = gr.Textbox(label="비밀번호", type="password", placeholder="가입 시 10~128자")
            with gr.Row():
                login = gr.Button("로그인", variant="primary")
                signup = gr.Button("회원가입")
        with gr.Group(visible=False) as workspace:
            with gr.Row():
                account = gr.Textbox(label="로그인 계정", interactive=False, scale=4)
                logout = gr.Button("로그아웃", scale=1)
            with gr.Row():
                with gr.Column(scale=1, min_width=240):
                    persona = gr.Dropdown(label="새 대화의 위인", choices=[])
                    create = gr.Button("새 대화 시작", variant="primary")
                    conversations = gr.Dropdown(label="이전 대화", choices=[])
                    refresh = gr.Button("목록 새로고침")
                    gr.Markdown(
                        "대화마다 위인 한 명이 고정됩니다. 위인을 바꾸려면 새 대화를 만드세요."
                    )
                with gr.Column(scale=3):
                    chat = gr.Chatbot(
                        label="대화 기록",
                        height=480,
                        sanitize_html=True,
                        render_markdown=False,
                        allow_tags=False,
                        buttons=["copy"],
                    )
                    question = gr.Textbox(
                        label="오늘의 고민",
                        lines=3,
                        max_lines=8,
                        placeholder="어떤 이야기를 나누고 싶으신가요? (최대 2,000자)",
                    )
                    with gr.Row():
                        send = gr.Button("질문 보내기", variant="primary")
                        retry = gr.Button("결과 확인 / 다시 요청", visible=False)
        gr.Markdown("역사적 인물의 관점을 참고하는 AI 대화입니다. 실제 인물의 발언이 아닙니다.")
        timer = gr.Timer(2, active=False)
        outputs = [
            state,
            auth,
            workspace,
            account,
            persona,
            conversations,
            chat,
            question,
            notice,
            create,
            send,
            refresh,
            retry,
            timer,
        ]

        def render(session, draft=None):
            logged_in = bool(session.token)
            unresolved = bool(session.pending_id and session.pending_status != "failed")
            locked = session.sending or unresolved
            choices = [
                (
                    f"{c['persona']['name']} · "
                    f"{c['created_at'][:16].replace('T', ' ')} UTC · #{c['id']}",
                    c["id"],
                )
                for c in session.conversations
            ]
            return (
                session,
                gr.update(visible=not logged_in),
                gr.update(visible=logged_in),
                session.email,
                gr.update(
                    choices=[
                        (f"{p['name']} — {p['description']}", p["id"]) for p in session.personas
                    ],
                    interactive=not locked,
                ),
                gr.update(
                    choices=choices, value=session.conversation_id, interactive=not session.sending
                ),
                session.messages(),
                gr.update(
                    **({"value": draft} if draft is not None else {}), interactive=not locked
                ),
                session.notice,
                gr.update(interactive=not locked),
                gr.update(interactive=logged_in and bool(session.conversation_id) and not locked),
                gr.update(interactive=not session.sending),
                gr.update(visible=bool(session.pending_id), interactive=not session.sending),
                gr.update(
                    active=logged_in
                    and bool(session.pending_id)
                    and session.pending_status == "processing"
                    and not session.sending
                ),
            )

        async def do_login(session, email, password):
            await controller.login(session, email, password)
            return (*render(session, ""), "")

        async def do_signup(session, email, password):
            await controller.signup(session, email, password)
            return (*render(session), "")

        async def do_logout(session):
            await controller.logout(session)
            return render(session, "" if not session.token else None)

        async def do_create(session, selected):
            if not selected:
                session.notice = "먼저 위인을 선택해 주세요."
                return render(session)
            await controller.new_conversation(session, selected)
            return render(session, "")

        async def do_open(session, cid):
            await controller.open_conversation(session, cid)
            return render(session, session.pending_question)

        async def do_refresh(session):
            await controller.refresh(session)
            return render(session)

        async def do_send(session, content):
            if not controller.prepare_send(session, content):
                yield render(session)
                return
            snapshot = session.snapshot()
            yield render(session, content)
            await controller.transmit(session)
            if not session.token or session.conversation_id is None:
                yield render(session, "")
            elif session.matches(snapshot):
                yield render(session, "" if not session.pending_id else content)
            else:
                yield tuple(gr.skip() for _ in outputs)

        async def do_retry(session, content):
            ready = await controller.retry(session, content)
            snapshot = session.snapshot()
            yield render(session)
            if ready:
                await controller.transmit(session)
                if not session.token or session.conversation_id is None:
                    yield render(session, "")
                elif session.matches(snapshot):
                    yield render(session, "" if not session.pending_id else content)
                else:
                    yield tuple(gr.skip() for _ in outputs)

        async def poll(session):
            snapshot = session.snapshot()
            had_pending = bool(session.pending_id)
            if session.pending_status == "processing":
                await controller.reconcile(session)
            if not session.matches(snapshot) and session.token:
                return tuple(gr.skip() for _ in outputs)
            draft = None
            if not session.token or (had_pending and not session.pending_id):
                draft = ""
            elif session.pending_status == "failed":
                draft = session.pending_question
            return render(session, draft)

        options = {"outputs": outputs, "concurrency_limit": None, "api_visibility": "private"}
        login.click(
            do_login, [state, email, password], **{**options, "outputs": [*outputs, password]}
        )
        signup.click(
            do_signup, [state, email, password], **{**options, "outputs": [*outputs, password]}
        )
        logout.click(do_logout, [state], **options)
        create.click(do_create, [state, persona], **options)
        conversations.input(do_open, [state, conversations], **options)
        refresh.click(do_refresh, [state], **options)
        send.click(do_send, [state, question], **options)
        question.submit(do_send, [state, question], **options)
        retry.click(do_retry, [state, question], **options)
        timer.tick(poll, [state], **options, show_progress="hidden")
        demo.load(lambda session: render(session), [state], **options)
    return demo.queue(default_concurrency_limit=32, max_size=128)


if __name__ == "__main__":
    config = UISettings()
    build_ui(config).launch(
        server_name=config.gradio_server_name,
        server_port=config.gradio_server_port,
        share=False,
        show_error=False,
        theme=gr.themes.Soft(),
    )
