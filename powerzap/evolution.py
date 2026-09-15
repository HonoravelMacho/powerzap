"""Cliente HTTP para a Evolution API."""
import base64
import mimetypes
import os

import requests

MEDIA_EXTENSIONS = {
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".webp": "image", ".bmp": "image", ".svg": "image",
    ".mp4": "video", ".avi": "video", ".mov": "video", ".mkv": "video",
    ".mp3": "audio", ".ogg": "audio", ".wav": "audio", ".opus": "audio",
    ".m4a": "audio", ".aac": "audio",
    ".pdf": "document", ".doc": "document", ".docx": "document",
    ".xls": "document", ".xlsx": "document", ".ppt": "document",
    ".pptx": "document", ".txt": "document", ".rtf": "document",
    ".csv": "document", ".zip": "document", ".rar": "document",
    ".7z": "document", ".odt": "document", ".ods": "document",
    # Qualquer extensão desconhecida cai em "document" (aceita qualquer arquivo).
}


def detect_media_type(path: str, fallback: str | None = None) -> str:
    if fallback in ("image", "video", "audio", "document"):
        return fallback
    ext = os.path.splitext(path or "")[1].lower()
    return MEDIA_EXTENSIONS.get(ext, "document")


def guess_mimetype(path: str) -> str:
    mime, _ = mimetypes.guess_type(path or "")
    return mime or "application/octet-stream"


class EvolutionError(Exception):
    pass


class EvolutionAPI:
    def __init__(self, url: str, api_key: str, instance: str, timeout: float = 30.0):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.instance = instance
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["apikey"] = self.api_key
        return h

    def _request(self, method: str, path: str, json_body=None,
                 timeout: float | None = None):
        url = f"{self.url}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), json=json_body,
                timeout=self.timeout if timeout is None else timeout,
            )
        except requests.RequestException as e:
            raise EvolutionError(f"Falha de conexão com a Evolution API: {e}") from e
        if resp.status_code >= 400:
            raise EvolutionError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        # Algumas respostas (ex: 204 ou HTML de erro) não têm JSON válido.
        try:
            return resp.json()
        except Exception:
            if not resp.text or not resp.text.strip():
                return {}
            raise EvolutionError(f"Resposta inválida da API: {resp.text[:200]}")

    # ---- Instância / Conexão ----

    def create_instance(self) -> dict:
        """Cria a instância já solicitando QR Code."""
        return self._request(
            "POST", "/instance/create",
            {
                "instanceName": self.instance,
                "integration": "WHATSAPP-BAILEYS",
                "qrcode": True,
            },
        )

    def connect_qr(self) -> str:
        """Retorna o QR Code em data-URI base64 para pareamento."""
        data = self._request("GET", f"/instance/connect/{self.instance}", timeout=60)
        if not isinstance(data, dict):
            raise EvolutionError("Resposta inesperada ao gerar QR Code.")
        b64 = data.get("base64") or ""
        qrcode = data.get("qrcode")
        if not b64 and isinstance(qrcode, dict):
            b64 = qrcode.get("base64") or ""
        if not b64 and isinstance(qrcode, str):
            b64 = qrcode
        if not isinstance(b64, str):
            b64 = ""
        # Algumas versões retornam {"code": "..."} (texto do QR) em vez de base64.
        if not b64 and data.get("code"):
            # Sem base64 não dá para exibir imagem; informa para usar o app.
            raise EvolutionError(
                "API retornou código texto sem imagem. Tente gerar de novo."
            )
        if not b64:
            # Pode já estar conectada: confirma antes de falhar.
            try:
                if self.is_connected():
                    raise EvolutionError("Instância já está conectada. Confira o status.")
            except EvolutionError:
                raise
            except Exception:
                pass
            raise EvolutionError("Resposta sem QR Code. A instância pode já estar conectada.")
        if not b64.startswith("data:"):
            b64 = "data:image/png;base64," + b64
        return b64

    def connection_state(self) -> dict:
        return self._request("GET", f"/instance/connectionState/{self.instance}")

    def is_connected(self) -> bool:
        try:
            state = self.connection_state()
        except Exception:
            return False
        info = state.get("instance") if isinstance(state, dict) else None
        if not isinstance(info, dict):
            info = state if isinstance(state, dict) else {}
        return str(info.get("state", "")).lower() == "open"

    def logout(self) -> dict:
        return self._request("DELETE", f"/instance/logout/{self.instance}")

    # ---- Envio ----

    def send_text(self, number: str, text: str) -> dict:
        from powerzap import db as _db
        number = _db.normalize_number(number)
        return self._request(
            "POST", f"/message/sendText/{self.instance}",
            {"number": number, "text": text},
        )

    def send_media(self, number: str, media_path: str, mediatype: str | None = None,
                   caption: str | None = None, mimetype: str | None = None) -> dict:
        """Envia imagem, vídeo, áudio ou documento (PDF etc).

        A Evolution v2 espera POST /message/sendMedia/{instance} com
        number, mediatype, media (base64), caption e fileName.
        Envia fileName + filename (compat com versões antigas) e
        sempre inclui caption/mimetype para evitar erro 400.
        Aceita qualquer tipo de arquivo (desconhecidos vão como document).
        """
        from powerzap import db as _db
        number = _db.normalize_number(number)
        if not media_path or not os.path.exists(media_path):
            raise EvolutionError(f"Arquivo de mídia não encontrado: {media_path}")
        mediatype = detect_media_type(media_path, mediatype)
        if mediatype not in ("image", "video", "audio", "document"):
            mediatype = "document"
        if not mimetype:
            mimetype = guess_mimetype(media_path)
        with open(media_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        fname = os.path.basename(media_path)
        payload = {
            "number": number,
            "mediatype": mediatype,
            "media": b64,
            "mimetype": mimetype,
            "fileName": fname,
            "filename": fname,
            "caption": caption or "",
        }
        return self._request(
            "POST", f"/message/sendMedia/{self.instance}", payload, timeout=120,
        )

    # ---- Contatos e grupos ----

    @staticmethod
    def _parse_contact_row(r: dict) -> dict | None:
        if not isinstance(r, dict):
            return None
        jid = (
            r.get("remoteJid") or r.get("id") or r.get("jid")
            or r.get("remoteJidAlt") or r.get("lid") or ""
        )
        # findContacts pode retornar só phoneNumber sem JID completo.
        if (not jid or "@" not in str(jid)):
            phone = (
                r.get("phoneNumber") or r.get("number") or r.get("phone")
                or r.get("pushNumber") or ""
            )
            phone = "".join(ch for ch in str(phone) if ch.isdigit())
            if phone:
                jid = f"{phone}@s.whatsapp.net"
        jid = str(jid or "")
        if not jid or "@" not in jid:
            return None
        # Filtra status/stories e broadcast
        if "@broadcast" in jid or "status@" in jid or "@newsletter" in jid:
            return None
        is_group = jid.endswith("@g.us")
        name = (
            r.get("pushName") or r.get("name") or r.get("subject")
            or r.get("pushname") or r.get("chatName") or r.get("fullName")
            or r.get("contactName") or r.get("push_name") or r.get("notify")
            or ""
        )
        name = str(name or "").strip()
        number = jid if is_group else jid.split("@")[0].split(":")[0]
        number = str(number or "").strip()
        if not number:
            return None
        # Remove sufixo de LID se vier junto (ex: "123_456").
        return {"number": number, "name": name, "is_group": is_group}

    def find_contacts(self) -> list:
        """Lista contatos individuais (endpoint original)."""
        try:
            data = self._request("POST", f"/chat/findContacts/{self.instance}",
                                 {}, timeout=120)
        except EvolutionError:
            return []
        rows = data.get("contacts") if isinstance(data, dict) else (data or [])
        if isinstance(rows, dict):
            rows = list(rows.values())
        out = []
        for r in (rows or []):
            parsed = self._parse_contact_row(r) if isinstance(r, dict) else None
            if parsed and not parsed["is_group"]:
                out.append(parsed)
        return out

    def fetch_groups(self) -> list:
        """Busca grupos via GET /group/fetchAllGroups."""
        try:
            data = self._request(
                "GET", f"/group/fetchAllGroups/{self.instance}?getParticipants=false",
                timeout=120)
        except EvolutionError:
            return []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("groups", data.get("data", data))
            if isinstance(rows, dict):
                # Algumas versões retornam {"1203...@g.us": {...}}
                rows = list(rows.values())
        else:
            rows = []
        out = []
        for r in (rows or []):
            if not isinstance(r, dict):
                continue
            jid = r.get("id") or r.get("remoteJid") or r.get("jid") or ""
            subject = (r.get("subject") or r.get("name") or "").strip()
            if not jid or "@" not in str(jid):
                continue
            if not str(jid).endswith("@g.us"):
                continue
            out.append({"number": str(jid), "name": str(subject), "is_group": True})
        return out

    def find_chats(self) -> list:
        """Busca conversas (inclui grupos e o próprio chat de teste)."""
        try:
            data = self._request("POST", f"/chat/findChats/{self.instance}",
                                 {}, timeout=120)
        except EvolutionError:
            return []
        rows = data.get("chats") if isinstance(data, dict) else (data or [])
        out = []
        for r in (rows or []):
            if not isinstance(r, dict):
                continue
            parsed = self._parse_contact_row(r)
            if parsed:
                out.append(parsed)
        return out

    @staticmethod
    def _extract_owner(info: dict) -> str | None:
        """Extrai número do dono de um dicionário de instância/estado."""
        if not isinstance(info, dict):
            return None
        for key in ("ownerJid", "owner", "wuid", "ownerJidFormatted",
                    "number", "phoneNumber", "phone"):
            val = info.get(key)
            if isinstance(val, str) and val.strip():
                v = val.strip()
                if "@" in v:
                    num = v.split("@")[0].split(":")[0].strip()
                    if num:
                        return num
                digits = "".join(ch for ch in v if ch.isdigit())
                # Evita confundir IDs longos de grupo com número próprio.
                if digits and 8 <= len(digits) <= 15:
                    return digits
        nested = info.get("instance")
        if isinstance(nested, dict):
            found = EvolutionAPI._extract_owner(nested)
            if found:
                return found
        return None

    def fetch_instances(self) -> list:
        """Retorna lista de instâncias (contém ownerJid/número próprio)."""
        try:
            data = self._request(
                "GET", f"/instance/fetchInstances?instanceName={self.instance}",
                timeout=30)
        except EvolutionError:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Formatos possíveis: {"instance": {...}} ou lista aninhada.
            if "instance" in data and isinstance(data["instance"], dict):
                return [data]
            for v in data.values():
                if isinstance(v, list):
                    return v
        return []

    def fetch_owner_number(self) -> str | None:
        """Tenta descobrir o próprio número (para mensagem de teste).

        Ordem: connectionState -> fetchInstances (fonte confiável do
        ownerJid) -> configurações locais. Nunca retorna vazio que
        quebraria o seletor visual.
        """
        # 1) Estado da conexão (algumas versões trazem ownerJid aqui).
        try:
            state = self.connection_state()
            info = state.get("instance") if isinstance(state, dict) else None
            owner = self._extract_owner(info if isinstance(info, dict) else state)
            if owner:
                return owner
        except Exception:
            pass
        # 2) fetchInstances — fonte oficial do número próprio.
        try:
            for row in self.fetch_instances():
                if not isinstance(row, dict):
                    continue
                inst = row.get("instance") if isinstance(row.get("instance"), dict) else row
                # Filtra pela instância atual quando há várias.
                name = str(inst.get("instanceName") or inst.get("name") or "")
                if name and name != self.instance:
                    continue
                owner = self._extract_owner(inst)
                if owner:
                    return owner
            # Segunda passada sem filtrar nome (caso o nome venha diferente).
            for row in self.fetch_instances():
                if not isinstance(row, dict):
                    continue
                inst = row.get("instance") if isinstance(row.get("instance"), dict) else row
                owner = self._extract_owner(inst)
                if owner:
                    return owner
        except Exception:
            pass
        # 3) Número salvo manualmente em Ajustes.
        try:
            from powerzap import db as _db
            saved = (_db.get_settings().get("my_number") or "").strip()
            if saved:
                return _db.normalize_number(saved)
        except Exception:
            pass
        return None

    def find_all(self) -> list:
        """Agrega contatos + chats + grupos, deduplicando por número.

        Robusto a falhas parciais: se um endpoint falhar ou voltar
        vazio (comum logo após sincronizar via QR Code), os outros
        ainda alimentam o seletor visual. Garante ainda o próprio
        número e o número salvo em Ajustes.
        """
        merged: dict = {}
        for source in (self.find_contacts, self.find_chats, self.fetch_groups):
            try:
                rows = source()
            except Exception:
                continue
            for ct in (rows or []):
                if not isinstance(ct, dict) or not ct.get("number"):
                    continue
                key = ct["number"]
                if key not in merged or (not merged[key]["name"] and ct["name"]):
                    merged[key] = ct
        # Garante o próprio número na lista para teste
        owner = None
        try:
            owner = self.fetch_owner_number()
        except Exception:
            owner = None
        if owner and owner not in merged:
            merged[owner] = {"number": owner, "name": "Você (este número)",
                             "is_group": False}
        # Fallback final: número salvo manualmente nunca some da lista.
        try:
            from powerzap import db as _db
            saved = (_db.get_settings().get("my_number") or "").strip()
            saved = _db.normalize_number(saved) if saved else ""
            if saved and saved not in merged:
                label = "Você (este número)" if saved == owner else "Meu número"
                merged[saved] = {"number": saved, "name": label,
                                 "is_group": False}
        except Exception:
            pass
        return list(merged.values())
