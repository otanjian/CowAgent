class WorkbenchUploadHandler:
    """POST /api/workbench/upload - Upload workbench data files.

    Request body (JSON):
    {
        files: {
            type: { filename: str, content: str }
        },
        summary: {},
        session_id: ""
    }

    For binary files (Excel, PDF, etc.), content should be base64-encoded.
    The handler auto-detects base64 and decodes to binary.
    """

    def POST(self):
        _require_auth()
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            import tempfile
            import uuid
            import base64

            data = web.data()
            if not data:
                return json.dumps({"status": "error", "message": "No data received"})

            body = json.loads(data)
            files_data = body.get("files", {})
            summary = body.get("summary", {})
            session_id = body.get("session_id", "")

            if not files_data:
                return json.dumps({"status": "error", "message": "No files provided"})

            # Save to workspace tmp directory so the AI agent can read the file
            workspace_root = _get_workspace_root()
            upload_dir = os.path.join(workspace_root, "tmp", "workbench", session_id or str(uuid.uuid4())[:8])
            os.makedirs(upload_dir, exist_ok=True)

            saved_files = {}
            for file_type, file_info in files_data.items():
                filename = file_info.get("filename", f"{file_type}.csv")
                content = file_info.get("content", "")
                is_base64 = file_info.get("is_base64", False)

                filepath = os.path.join(upload_dir, filename)

                if is_base64:
                    # Decode base64 to binary (for Excel, PDF, etc.)
                    try:
                        binary_data = base64.b64decode(content)
                        with open(filepath, "wb") as f:
                            f.write(binary_data)
                    except Exception:
                        return json.dumps({"status": "error", "message": f"Invalid base64 content for {filename}"})
                else:
                    # Plain text content (for CSV, JSON, etc.)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(content)

                saved_files[file_type] = filepath

            # 项目根目录（技能脚本所在位置），供前端注入提示词中的脚本绝对路径
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            return json.dumps({
                "status": "success",
                "files": saved_files,
                "upload_dir": upload_dir,
                "summary": summary,
                "file_path": next(iter(saved_files.values())) if saved_files else "",
                "project_root": project_root
            }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[WebChannel] Workbench upload error: {e}")
            return json.dumps({"status": "error", "message": str(e)})
