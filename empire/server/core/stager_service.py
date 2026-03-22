import asyncio
import copy
import logging
import typing
import uuid
import warnings
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from empire.server.core.config.config_manager import empire_config
from empire.server.core.db import models
from empire.server.utils.option_util import set_options, validate_options

log = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from empire.server.common.empire import MainMenu
    from empire.server.core.download_service import DownloadService
    from empire.server.core.listener_service import ListenerService
    from empire.server.core.stager_template_service import StagerTemplateService


class StagerService:
    def __init__(self, main_menu: "MainMenu"):
        self.main_menu = main_menu

        self.stager_template_service: StagerTemplateService = (
            main_menu.stagertemplatesv2
        )
        self.listener_service: ListenerService = main_menu.listenersv2
        self.download_service: DownloadService = main_menu.downloadsv2

    @staticmethod
    def get_all(db: Session):
        return db.query(models.Stager).all()

    @staticmethod
    def get_by_id(db: Session, uid: int):
        return db.query(models.Stager).filter(models.Stager.id == uid).first()

    @staticmethod
    def get_by_name(db: Session, name: str):
        return db.query(models.Stager).filter(models.Stager.name == name).first()

    def validate_stager_options(
        self, db: Session, template: str, params: dict
    ) -> tuple[Any | None, str | None]:
        """
        Validates the new listener's options. Constructs a new "Listener" object.
        :param template:
        :param params:
        :return: (Stager, error)
        """
        if not self.stager_template_service.get_stager_template(template):
            return None, f"Stager Template {template} not found"

        if params.get("Listener") and not self.listener_service.get_by_name(
            db, params["Listener"]
        ):
            return None, f"Listener {params['Listener']} not found"

        template_instance = self.stager_template_service.new_instance(template)
        cleaned_options, err = validate_options(
            template_instance.options, params, db, self.download_service
        )

        if err:
            return None, err

        revert_options = {}
        for key, value in template_instance.options.items():
            revert_options[key] = template_instance.options[key]["Value"]
            template_instance.options[key]["Value"] = value

        set_options(template_instance, cleaned_options)

        # stager instances don't have a validate method. but they could

        return template_instance, None

    @staticmethod
    def _flatten_options(template_instance):
        """Return a flat {key: value} dict from template option metadata."""
        options = copy.deepcopy(template_instance.options)
        return {key: meta["Value"] for key, meta in options.items()}

    @staticmethod
    def _add_download(db, stager, generated):
        """Create a Download record and attach it to *stager*."""
        download = models.Download(
            location=str(generated),
            filename=generated.name,
            size=generated.stat().st_size,
        )
        db.add(download)
        db.flush()
        stager.downloads.append(download)

    def _persist_new_stager(  # noqa: PLR0913
        self, db, stager_req, template_instance, generated, user_id, save
    ):
        """Create Stager + Download DB records after generation."""
        stager_options = self._flatten_options(template_instance)

        db_stager = models.Stager(
            name=stager_req.name,
            module=stager_req.template,
            options=stager_options,
            one_liner=not stager_options.get("OutFile", ""),
            user_id=user_id,
        )

        self._add_download(db, db_stager, generated)

        if save:
            db.add(db_stager)
            db.flush()
        else:
            db_stager.id = 0

        return db_stager, None

    def _persist_updated_stager(self, db, db_stager, template_instance, generated):
        """Update existing Stager options + add new Download."""
        db_stager.options = self._flatten_options(template_instance)
        self._add_download(db, db_stager, generated)
        return db_stager, None

    def _validate_create(self, db, stager_req, save):
        """Shared validation for create_stager / create_stager_async."""
        if save and self.get_by_name(db, stager_req.name):
            return None, f"Stager with name {stager_req.name} already exists."
        return self.validate_stager_options(db, stager_req.template, stager_req.options)

    def _validate_update(self, db, db_stager, stager_req):
        """Shared validation for update_stager / update_stager_async."""
        if stager_req.name != db_stager.name:
            if not self.get_by_name(db, stager_req.name):
                db_stager.name = stager_req.name
            else:
                return None, f"Stager with name {stager_req.name} already exists."
        return self.validate_stager_options(db, db_stager.module, stager_req.options)

    def create_stager(self, db: Session, stager_req, save: bool, user_id: int):
        """.. deprecated:: Use ``create_stager_async`` instead. Will be removed in 7.0."""
        warnings.warn(
            "create_stager() is deprecated, use create_stager_async()",
            DeprecationWarning,
            stacklevel=2,
        )
        template_instance, err = self._validate_create(db, stager_req, save)
        if err:
            return None, err

        generated, err = self.generate_stager(template_instance)
        if err:
            return None, err

        return self._persist_new_stager(
            db, stager_req, template_instance, generated, user_id, save
        )

    async def create_stager_async(
        self, db: Session, stager_req, save: bool, user_id: int
    ):
        """Like ``create_stager`` but offloads blocking generation to a thread."""
        template_instance, err = self._validate_create(db, stager_req, save)
        if err:
            return None, err

        try:
            generated, err = await asyncio.to_thread(
                self.generate_stager, template_instance
            )
        except Exception as e:
            msg = f"Stager generation failed: {type(e).__name__}: {e}"
            log.error(msg, exc_info=True)
            return None, msg
        if err:
            return None, err

        return self._persist_new_stager(
            db, stager_req, template_instance, generated, user_id, save
        )

    def update_stager(self, db: Session, db_stager: models.Stager, stager_req):
        """.. deprecated:: Use ``update_stager_async`` instead. Will be removed in 7.0."""
        warnings.warn(
            "update_stager() is deprecated, use update_stager_async()",
            DeprecationWarning,
            stacklevel=2,
        )
        template_instance, err = self._validate_update(db, db_stager, stager_req)
        if err:
            return None, err

        generated, err = self.generate_stager(template_instance)
        if err:
            return None, err

        return self._persist_updated_stager(db, db_stager, template_instance, generated)

    async def update_stager_async(
        self, db: Session, db_stager: models.Stager, stager_req
    ):
        """Like ``update_stager`` but offloads blocking generation to a thread."""
        template_instance, err = self._validate_update(db, db_stager, stager_req)
        if err:
            return None, err

        try:
            generated, err = await asyncio.to_thread(
                self.generate_stager, template_instance
            )
        except Exception as e:
            msg = f"Stager generation failed: {type(e).__name__}: {e}"
            log.error(msg, exc_info=True)
            return None, msg
        if err:
            return None, err

        return self._persist_updated_stager(db, db_stager, template_instance, generated)

    def generate_stager(self, template_instance):
        resp = template_instance.generate()

        # todo generate should return error response much like listener validate
        #  options should.
        if not resp:
            return None, "Error generating"

        out_file = template_instance.options.get("OutFile", {}).get("Value")
        file_name = Path(out_file).name if out_file else f"{uuid.uuid4()}.txt"

        file_name = (
            empire_config.directories.downloads / "generated-stagers" / file_name
        )
        file_name.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if isinstance(resp, str) else "wb"
        with file_name.open(mode) as f:
            f.write(resp)

        return file_name, None

    @staticmethod
    def delete_stager(db: Session, stager: models.Stager):
        db.delete(stager)
