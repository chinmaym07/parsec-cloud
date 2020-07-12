# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import trio
from enum import IntEnum
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QWidget

from parsec.core.backend_connection import BackendNotAvailable, BackendConnectionError
from parsec.core.invite import InviteError
from parsec.core.gui.trio_thread import JobResultError, ThreadSafeQtSignal, QtToTrioJob
from parsec.core.gui.custom_dialogs import show_error, GreyedDialog, show_info
from parsec.core.gui.lang import translate as _
from parsec.core.gui import desktop
from parsec.core.gui.ui.greet_device_widget import Ui_GreetDeviceWidget
from parsec.core.gui.ui.greet_device_code_exchange_widget import Ui_GreetDeviceCodeExchangeWidget
from parsec.core.gui.ui.greet_device_instructions_widget import Ui_GreetDeviceInstructionsWidget


class Greeter:
    class Step(IntEnum):
        WaitPeer = 1
        GetGreeterSas = 2
        WaitPeerTrust = 3
        GetClaimerSas = 4
        SignifyTrust = 5

    def __init__(self):
        self.main_mc_send, self.main_mc_recv = trio.open_memory_channel(0)
        self.job_mc_send, self.job_mc_recv = trio.open_memory_channel(0)

    async def run(self, core, token):
        try:
            r = await self.main_mc_recv.receive()

            assert r == self.Step.WaitPeer
            try:
                in_progress_ctx = await core.start_greeting_device(token=token)
                await self.job_mc_send.send((True, None))
            except Exception as exc:
                await self.job_send.send((False, exc))

            r = await self.main_mc_recv.receive()

            assert r == self.Step.GetGreeterSas
            await self.job_mc_send.send(in_progress_ctx.greeter_sas)

            r = await self.main_mc_recv.receive()

            assert r == self.Step.WaitPeerTrust
            try:
                in_progress_ctx = await in_progress_ctx.do_wait_peer_trust()
                await self.job_mc_send.send((True, None))
            except Exception as exc:
                await self.job_mc_send.send((False, exc))

            r = await self.main_mc_recv.receive()

            assert r == self.Step.GetClaimerSas
            try:
                choices = in_progress_ctx.generate_claimer_sas_choices(size=4)
                await self.job_mc_send.send((True, None, in_progress_ctx.claimer_sas, choices))
            except Exception as exc:
                await self.job_mc_send.send((False, exc, None, None))

            r = await self.main_mc_recv.receive()

            assert r == self.Step.SignifyTrust
            try:
                in_progress_ctx = await in_progress_ctx.do_signify_trust()
                in_progress_ctx = await in_progress_ctx.do_get_claim_requests()
                await in_progress_ctx.do_create_new_device(
                    author=core.device, device_label=in_progress_ctx.requested_device_label
                )
                await self.job_mc_send.send((True, None))
            except InviteError as exc:
                await self.job_mc_send.send((False, exc))
            except Exception as exc:
                await self.job_mc_send.send((False, exc))

        except BackendNotAvailable as exc:
            raise JobResultError(status="backend-not-available", origin=exc)

    async def wait_peer(self):
        await self.main_mc_send.send(self.Step.WaitPeer)
        r, exc = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="wait-peer-failed", origin=exc)

    async def get_greeter_sas(self):
        await self.main_mc_send.send(self.Step.GetGreeterSas)
        greeter_sas = await self.job_mc_recv.receive()
        return greeter_sas

    async def wait_peer_trust(self):
        await self.main_mc_send.send(self.Step.WaitPeerTrust)
        r, exc = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="wait-peer-trust-failed", origin=exc)

    async def get_claimer_sas(self):
        await self.main_mc_send.send(self.Step.GetClaimerSas)
        r, exc, claimer_sas, choices = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="get-claimer-sas-failed", origin=exc)
        return claimer_sas, choices

    async def signify_trust(self):
        await self.main_mc_send.send(self.Step.SignifyTrust)
        r, exc = await self.job_mc_recv.receive()
        if not r:
            raise JobResultError(status="signify-trust-failed", origin=exc)


async def _do_send_email(core):
    try:
        return await core.new_device_invitation(send_email=True)
    except BackendNotAvailable as exc:
        raise JobResultError("offline") from exc
    except BackendConnectionError as exc:
        raise JobResultError("error") from exc


class GreetDeviceInstructionsWidget(QWidget, Ui_GreetDeviceInstructionsWidget):
    succeeded = pyqtSignal()
    failed = pyqtSignal()

    wait_peer_success = pyqtSignal()
    wait_peer_error = pyqtSignal()

    send_email_success = pyqtSignal(QtToTrioJob)
    send_email_error = pyqtSignal(QtToTrioJob)

    def __init__(self, jobs_ctx, greeter, invite_addr, core):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.greeter = greeter
        self.invite_addr = invite_addr
        self.core = core

        self.wait_peer_job = None
        self.wait_peer_success.connect(self._on_wait_peer_success)
        self.wait_peer_error.connect(self._on_wait_peer_error)
        self.send_email_success.connect(self._on_send_email_success)
        self.send_email_error.connect(self._on_send_email_error)
        self.button_start.clicked.connect(self._on_button_start_clicked)
        self.button_send_email.clicked.connect(self._on_button_send_email_clicked)
        self.button_copy_addr.clicked.connect(self._on_copy_addr_clicked)

    def _on_copy_addr_clicked(self):
        desktop.copy_to_clipboard(self.invite_addr.to_url())
        show_info(self, _("TEXT_GREET_DEVICE_ADDR_COPIED_TO_CLIPBOARD"))

    def _on_button_send_email_clicked(self):
        self.button_send_email.setDisabled(True)
        self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "send_email_success", QtToTrioJob),
            ThreadSafeQtSignal(self, "send_email_error", QtToTrioJob),
            _do_send_email,
            core=self.core,
        )

    def _on_send_email_success(self, job):
        # In theory the invitation address shouldn't have changed, but better safe than sorry
        self.invite_addr = job.ret
        self.button_send_email.setText(_("TEXT_GREET_DEVICE_EMAIL_SENT"))
        self.button_send_email.setDisabled(True)

    def _on_send_email_error(self, job):
        show_error(self, _("TEXT_GREET_DEVICE_SEND_EMAIL_ERROR"), exception=job.exc)
        self.button_send_email.setDisabled(False)

    def _on_button_start_clicked(self):
        self.button_start.setDisabled(True)
        self.button_send_email.setDisabled(True)
        self.button_start.setText(_("TEXT_GREET_DEVICE_WAITING"))
        self.wait_peer_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "wait_peer_success"),
            ThreadSafeQtSignal(self, "wait_peer_error"),
            self.greeter.wait_peer,
        )

    def _on_wait_peer_success(self):
        assert self.wait_peer_job
        assert self.wait_peer_job.is_finished()
        assert self.wait_peer_job.status == "ok"
        self.greeter_sas = self.wait_peer_job.ret
        self.wait_peer_job = None
        self.succeeded.emit()

    def _on_wait_peer_error(self):
        assert self.wait_peer_job
        assert self.wait_peer_job.is_finished()
        assert self.wait_peer_job.status != "ok"
        if self.wait_peer_job.status != "cancelled":
            msg = _("TEXT_GREET_DEVICE_WAIT_PEER_ERROR")
            exc = None
            if self.wait_peer_job.exc:
                exc = self.wait_peer_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.wait_peer_job = None
            self.failed.emit()
        else:
            self.wait_peer_job = None


class GreetDeviceCodeExchangeWidget(QWidget, Ui_GreetDeviceCodeExchangeWidget):
    succeeded = pyqtSignal()
    failed = pyqtSignal()

    signify_trust_success = pyqtSignal()
    signify_trust_error = pyqtSignal()

    wait_peer_trust_success = pyqtSignal()
    wait_peer_trust_error = pyqtSignal()

    get_claimer_sas_success = pyqtSignal()
    get_claimer_sas_error = pyqtSignal()

    get_greeter_sas_success = pyqtSignal()
    get_greeter_sas_error = pyqtSignal()

    def __init__(self, jobs_ctx, greeter):
        super().__init__()
        self.setupUi(self)
        self.jobs_ctx = jobs_ctx
        self.greeter = greeter

        self.wait_peer_trust_job = None
        self.signify_trust_job = None
        self.get_claimer_sas_job = None
        self.get_greeter_sas_job = None

        self.widget_claimer_code.hide()

        font = self.line_edit_greeter_code.font()
        font.setBold(True)
        font.setLetterSpacing(QFont.PercentageSpacing, 180)
        self.line_edit_greeter_code.setFont(font)

        self.code_input_widget.good_code_clicked.connect(self._on_good_claimer_code_clicked)
        self.code_input_widget.wrong_code_clicked.connect(self._on_wrong_claimer_code_clicked)
        self.code_input_widget.none_clicked.connect(self._on_none_clicked)

        self.signify_trust_success.connect(self._on_signify_trust_success)
        self.signify_trust_error.connect(self._on_signify_trust_error)
        self.wait_peer_trust_success.connect(self._on_wait_peer_trust_success)
        self.wait_peer_trust_error.connect(self._on_wait_peer_trust_error)
        self.get_greeter_sas_success.connect(self._on_get_greeter_sas_success)
        self.get_greeter_sas_error.connect(self._on_get_greeter_sas_error)
        self.get_claimer_sas_success.connect(self._on_get_claimer_sas_success)
        self.get_claimer_sas_error.connect(self._on_get_claimer_sas_error)

        self.label_wait_info.hide()

    def _run_get_greeter_sas(self):
        self.get_greeter_sas_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "get_greeter_sas_success"),
            ThreadSafeQtSignal(self, "get_greeter_sas_error"),
            self.greeter.get_greeter_sas,
        )

    def _on_good_claimer_code_clicked(self):
        self.widget_claimer_code.hide()
        self.label_wait_info.show()
        self.signify_trust_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "signify_trust_success"),
            ThreadSafeQtSignal(self, "signify_trust_error"),
            self.greeter.signify_trust,
        )

    def _on_wrong_claimer_code_clicked(self):
        show_error(self, _("TEXT_GREET_DEVICE_INVALID_CODE_CLICKED"))
        self.failed.emit()

    def _on_none_clicked(self):
        show_info(self, _("TEXT_GREET_DEVICE_NONE_CODE_CLICKED"))
        self.failed.emit()

    def _on_get_greeter_sas_success(self):
        assert self.get_greeter_sas_job
        assert self.get_greeter_sas_job.is_finished()
        assert self.get_greeter_sas_job.status == "ok"
        greeter_sas = self.get_greeter_sas_job.ret
        self.line_edit_greeter_code.setText(str(greeter_sas))
        self.get_greeter_sas_job = None
        self.wait_peer_trust_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "wait_peer_trust_success"),
            ThreadSafeQtSignal(self, "wait_peer_trust_error"),
            self.greeter.wait_peer_trust,
        )

    def _on_get_greeter_sas_error(self):
        assert self.get_greeter_sas_job
        assert self.get_greeter_sas_job.is_finished()
        assert self.get_greeter_sas_job.status != "ok"
        if self.get_greeter_sas_job.status != "cancelled":
            msg = _("TEXT_GREET_DEVICE_GET_GREETER_SAS_ERROR")
            exc = None
            if self.get_greeter_sas_job.exc:
                exc = self.get_greeter_sas_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.failed.emit()
            self.get_greeter_sas_job = None
        else:
            self.get_greeter_sas_job = None

    def _on_get_claimer_sas_success(self):
        assert self.get_claimer_sas_job
        assert self.get_claimer_sas_job.is_finished()
        assert self.get_claimer_sas_job.status == "ok"
        claimer_sas, choices = self.get_claimer_sas_job.ret
        self.get_claimer_sas_job = None
        self.widget_greeter_code.hide()
        self.widget_claimer_code.show()
        self.code_input_widget.set_choices(choices, claimer_sas)

    def _on_get_claimer_sas_error(self):
        assert self.get_claimer_sas_job
        assert self.get_claimer_sas_job.is_finished()
        assert self.get_claimer_sas_job.status != "ok"
        if self.get_claimer_sas_job.status != "cancelled":
            msg = _("TEXT_GREET_DEVICE_GET_CLAIMER_SAS_ERROR")
            exc = None
            if self.get_claimer_sas_job.exc:
                exc = self.get_claimer_sas_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.failed.emit()
            self.get_claimer_sas_job = None
        else:
            self.get_claimer_sas_job = None

    def _on_signify_trust_success(self):
        assert self.signify_trust_job
        assert self.signify_trust_job.is_finished()
        assert self.signify_trust_job.status == "ok"
        self.signify_trust_job = None
        self.succeeded.emit()

    def _on_signify_trust_error(self):
        assert self.signify_trust_job
        assert self.signify_trust_job.is_finished()
        assert self.signify_trust_job.status != "ok"
        if self.signify_trust_job.status != "cancelled":
            msg = _("TEXT_GREET_DEVICE_SIGNIFY_TRUST_ERROR")
            exc = None
            if self.signify_trust_job.exc:
                exc = self.signify_trust_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.failed.emit()
            self.signify_trust_job = None
        else:
            self.signify_trust_job = None

    def _on_wait_peer_trust_success(self):
        assert self.wait_peer_trust_job
        assert self.wait_peer_trust_job.is_finished()
        assert self.wait_peer_trust_job.status == "ok"
        self.wait_peer_trust_job = None
        self.get_claimer_sas_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "get_claimer_sas_success"),
            ThreadSafeQtSignal(self, "get_claimer_sas_error"),
            self.greeter.get_claimer_sas,
        )

    def _on_wait_peer_trust_error(self):
        assert self.wait_peer_trust_job
        assert self.wait_peer_trust_job.is_finished()
        assert self.wait_peer_trust_job.status != "ok"
        if self.wait_peer_trust_job.status != "cancelled":
            msg = _("TEXT_GREET_DEVICE_WAIT_PEER_TRUST_ERROR")
            exc = None
            if self.wait_peer_trust_job.exc:
                exc = self.wait_peer_trust_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
            self.failed.emit()
            self.wait_peer_trust_job = None
        else:
            self.wait_peer_trust_job = None


class GreetDeviceWidget(QWidget, Ui_GreetDeviceWidget):
    greeter_success = pyqtSignal()
    greeter_error = pyqtSignal()

    def __init__(self, core, jobs_ctx, invite_addr):
        super().__init__()
        self.setupUi(self)
        self.core = core
        self.jobs_ctx = jobs_ctx
        self.invite_addr = invite_addr
        self.dialog = None
        self.greeter = Greeter()
        self.greeter_job = None
        self.greeter_success.connect(self._on_greeter_success)
        self.greeter_error.connect(self._on_greeter_error)

        self.greet_device_instructions_widget = GreetDeviceInstructionsWidget(
            self.jobs_ctx, self.greeter, self.invite_addr, self.core
        )
        self.greet_device_instructions_widget.succeeded.connect(self._goto_page2)
        self.greet_device_instructions_widget.failed.connect(self._on_page_failure_reboot)

        self.greet_device_code_exchange_widget = GreetDeviceCodeExchangeWidget(
            self.jobs_ctx, self.greeter
        )
        self.greet_device_code_exchange_widget.succeeded.connect(self._on_finished)
        self.greet_device_code_exchange_widget.failed.connect(self._on_page_failure_reboot)

        self.main_layout.addWidget(self.greet_device_instructions_widget)
        self.main_layout.addWidget(self.greet_device_code_exchange_widget)

        self._run_greeter()

    def _run_greeter(self):
        self.greeter_job = self.jobs_ctx.submit_job(
            ThreadSafeQtSignal(self, "greeter_success"),
            ThreadSafeQtSignal(self, "greeter_error"),
            self.greeter.run,
            core=self.core,
            token=self.invite_addr.token,
        )
        self._goto_page1()

    def restart(self):
        self.cancel()
        # Replace moving parts
        self.greeter = Greeter()
        self.greet_device_instructions_widget.greeter = self.greeter
        self.greet_device_code_exchange_widget.greeter = self.greeter
        self._run_greeter()

    def _get_current_page(self):
        for page in (self.greet_device_instructions_widget, self.greet_device_code_exchange_widget):
            if not page.isHidden():
                return page

    def _on_page_failure_reboot(self):
        self.restart()

    def _on_page_failure_stop(self):
        self.dialog.accept()

    def _goto_page1(self):
        self.greet_device_instructions_widget.setHidden(False)
        self.greet_device_code_exchange_widget.setHidden(True)

    def _goto_page2(self):
        self.greet_device_instructions_widget.setHidden(True)
        self.greet_device_code_exchange_widget.setHidden(False)
        self.greet_device_code_exchange_widget._run_get_greeter_sas()

    def _on_finished(self):
        show_info(self, _("TEXT_DEVICE_GREET_SUCCESSFUL"))
        self.dialog.accept()

    def _on_greeter_success(self):
        assert self.greeter_job
        assert self.greeter_job.is_finished()
        assert self.greeter_job.status == "ok"
        self.greeter_job = None

    def _on_greeter_error(self):
        assert self.greeter_job
        assert self.greeter_job.is_finished()
        assert self.greeter_job.status != "ok"
        if self.greeter_job.status != "cancelled":
            msg = ""
            if self.greeter_job.status == "backend-not-available":
                msg = _("TEXT_INVITATION_BACKEND_NOT_AVAILABLE")
            else:
                msg = _("TEXT_GREET_DEVICE_UNKNOWN_ERROR")
            exc = None
            if self.greeter_job.exc:
                exc = self.greeter_job.exc.params.get("origin", None)
            show_error(self, msg, exception=exc)
        self.greeter_job = None
        self._on_page_failure_stop()

    def cancel(self):
        item = self.main_layout.itemAt(0)
        if item:
            current_page = item.widget()
            if current_page and getattr(current_page, "cancel", None):
                current_page.cancel()
        if self.greeter_job:
            self.greeter_job.cancel_and_join()

    def on_close(self):
        self.cancel()

    @classmethod
    def exec_modal(cls, core, jobs_ctx, invite_addr, parent, on_finished):
        w = cls(core=core, jobs_ctx=jobs_ctx, invite_addr=invite_addr)
        d = GreyedDialog(w, _("TEXT_GREET_DEVICE_TITLE"), parent=parent, width=1000)
        w.dialog = d

        d.finished.connect(on_finished)
        # Unlike exec_, show is asynchronous and works within the main Qt loop
        d.show()
        return w
