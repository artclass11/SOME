const form = document.getElementById("composer");
    const promptInput = document.getElementById("prompt");
    const messages = document.getElementById("messages");
    const sendButton = document.getElementById("send");
    const hint = document.getElementById("hint");
    const intro = document.getElementById("intro");
    const uploadInput = document.getElementById("upload");
    const activityToggle = document.getElementById("activity-toggle");
    const activityPanel = document.getElementById("activity-panel");
    const activityList = document.getElementById("activity-list");
    const quickPromptButtons = Array.from(document.querySelectorAll("[data-prompt]"));
    let activeRequests = 0;

    function beginRequest(statusText) {
      activeRequests += 1;
      sendButton.disabled = true;
      uploadInput.disabled = true;
      quickPromptButtons.forEach(button => { button.disabled = true; });
      if (statusText) hint.textContent = statusText;
    }

    function endRequest() {
      activeRequests = Math.max(0, activeRequests - 1);
      const busy = activeRequests > 0;
      sendButton.disabled = busy;
      uploadInput.disabled = busy;
      quickPromptButtons.forEach(button => { button.disabled = busy; });
      if (!busy) hint.textContent = "Your files stay in the local workspace.";
    }

    function el(tag, className, text) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    }

    function appendMessage(role, text, data, planId) {
      intro.classList.add("hidden");
      const wrap = el("article", "message " + role);
      wrap.append(el("div", "who", role === "user" ? "YOU" : "SOME"));
      wrap.append(el("div", "bubble" + (data && data.kind === "error" ? " error" : ""), text));
      if (data && data.result) wrap.append(renderResult(data.result, data.kind));
      if (data && data.kind === "plan" && planId) wrap.append(renderPlanActions(planId));
      if (data && data.receipt_id) {
        wrap.append(el("div", "who", "Receipt · " + data.receipt_id.slice(0, 12)));
      }
      messages.append(wrap);
      wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (data && data.receipt_id && !activityPanel.classList.contains("hidden")) {
        loadActivity().catch(() => {});
      }
      return wrap;
    }

    function renderResult(data, kind) {
      const box = el("div", "result");
      const title = kind === "error"
        ? "Operation details · review required"
        : data.action === "preview_organization"
          ? "Proposed changes · nothing moved yet"
          : (data.action || "Action") + " · verified output";
      box.append(el("div", "result-title", title));
      if (Array.isArray(data.files)) {
        const list = el("ul", "result-list");
        data.files.slice(0, 30).forEach(item => {
          const row = el("li", "");
          row.append(el("span", "", item.path));
          row.append(el("span", "subtle", formatBytes(item.size_bytes)));
          list.append(row);
        });
        if (!data.files.length) list.append(el("li", "subtle", "No files found yet."));
        box.append(list);
        if (data.truncated) box.append(el("p", "subtle", "Showing the first 30 files."));
      } else if (Array.isArray(data.duplicates)) {
        const list = el("ul", "result-list");
        data.duplicates.forEach((group, index) => {
          const row = el("li", "");
          row.append(el("span", "", group.join("  =  ")));
          row.append(el("span", "subtle", "Group " + (index + 1)));
          list.append(row);
        });
        if (!data.duplicates.length) list.append(el("li", "subtle", "No duplicate groups found."));
        box.append(list);
      } else if (Array.isArray(data.moves)) {
        const list = el("ul", "result-list");
        data.moves.slice(0, 50).forEach(move => {
          list.append(el("li", "", move.source + "  →  " + move.target));
        });
        if (!data.moves.length) list.append(el("li", "subtle", "No top-level files need moving."));
        if (data.moves.length > 50) box.append(el("p", "subtle", "Showing the first 50 planned moves."));
        box.append(list);
      } else if (data.action === "profile_csv") {
        const list = el("ul", "result-list");
        Object.entries(data.missing_by_column || {}).forEach(([name, count]) => {
          list.append(el("li", "", name + " — " + count + " missing"));
        });
        box.append(el("div", "subtle", data.columns.join(" · ")));
        box.append(list);
      } else {
        const fields = [];
        for (const [key, value] of Object.entries(data)) {
          if (key === "action" || value === null || typeof value === "object") continue;
          fields.push([key.replaceAll("_", " "), String(value)]);
        }
        if (fields.length) {
          const list = el("ul", "result-list");
          fields.forEach(([key, value]) => list.append(el("li", "", key + ": " + value)));
          box.append(list);
        }
      }
      const artifactPath = data.action === "clean_csv" ? data.output
        : (data.action === "create_note" || data.action === "upload_file") ? data.file
        : null;
      if (typeof artifactPath === "string" && artifactPath.length > 0) {
        const encodedPath = artifactPath.split("/").map(part => encodeURIComponent(part)).join("/");
        const link = el("a", "download-link", "Download file ↗");
        link.href = "/api/artifacts/" + encodedPath;
        link.setAttribute("download", "");
        box.append(link);
      }
      return box;
    }

    function renderPlanActions(planId) {
      const group = el("div", "plan-actions");
      const confirm = el("button", "primary", "Confirm file moves");
      confirm.type = "button";
      const cancel = el("button", "secondary", "Cancel");
      cancel.type = "button";
      let pending = false;
      let settled = false;

      function finishPlanNotice(message) {
        settled = true;
        group.replaceWith(el("div", "subtle", message));
        loadActivity().catch(() => {});
      }

      confirm.addEventListener("click", async () => {
        if (pending || settled || activeRequests > 0) return;
        pending = true;
        confirm.textContent = "Applying…";
        confirm.disabled = true;
        cancel.disabled = true;
        beginRequest("Applying approved file moves…");
        try {
          const result = await api(
            "/api/plans/" + encodeURIComponent(planId) + "/confirm",
            { method: "POST" }
          );
          settled = true;
          group.replaceWith(el("div", "subtle", "Plan completed. See Activity for the receipt."));
          appendMessage("assistant", result.message, result);
        } catch (error) {
          if (error.status === 404 || error.status === 409) {
            finishPlanNotice("This plan is no longer available. Check Activity before retrying.");
          } else {
            confirm.textContent = "Retry confirmation";
            appendMessage("assistant", error.message, { kind: "error" });
          }
        } finally {
          pending = false;
          endRequest();
          if (!settled) {
            confirm.disabled = false;
            cancel.disabled = false;
          }
        }
      });

      cancel.addEventListener("click", async () => {
        if (pending || settled || activeRequests > 0) return;
        pending = true;
        cancel.textContent = "Cancelling…";
        confirm.disabled = true;
        cancel.disabled = true;
        beginRequest("Cancelling the pending plan…");
        try {
          await api("/api/plans/" + encodeURIComponent(planId), { method: "DELETE" });
          settled = true;
          group.replaceWith(el("div", "subtle", "Cancelled. No files were moved."));
        } catch (error) {
          if (error.status === 404) {
            finishPlanNotice("This plan expired or was already handled. Check Activity for its outcome.");
          } else {
            cancel.textContent = "Retry cancellation";
            appendMessage("assistant", error.message, { kind: "error" });
          }
        } finally {
          pending = false;
          endRequest();
          if (!settled) {
            confirm.disabled = false;
            cancel.disabled = false;
          }
        }
      });
      group.append(confirm, cancel);
      return group;
    }

    function formatBytes(bytes) {
      if (bytes < 1024) return bytes + " B";
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
      return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    async function api(url, options) {
      const response = await fetch(url, {
        ...options,
        headers: { "Content-Type": "application/json", ...(options && options.headers ? options.headers : {}) }
      });
      let result;
      try { result = await response.json(); }
      catch (_) { throw new Error("The local service returned an unreadable response."); }
      if (!response.ok) {
        const error = new Error(result.detail || "The action failed.");
        error.status = response.status;
        throw error;
      }
      return result;
    }

    async function loadActivity() {
      activityList.replaceChildren(el("li", "subtle", "Loading receipts…"));
      try {
        const response = await api("/api/receipts?limit=30");
        activityList.replaceChildren();
        if (!response.items.length) {
          activityList.append(el("li", "subtle", "No actions recorded yet."));
          return;
        }
        response.items.forEach(receipt => {
          const item = el("li", "receipt-item");
          const details = el("div", "receipt-details");
          details.append(el("span", "", receipt.action.replaceAll("_", " ")));
          const state = el("span", "receipt-state " + (
            receipt.status === "failed" || receipt.status === "interrupted" ? "error" : "subtle"
          ), receipt.status);
          details.append(state);
          item.append(details);
          item.append(el("span", "subtle", receipt.summary));
          const timestamp = new Date(receipt.created_at);
          item.append(el("span", "subtle", Number.isNaN(timestamp.getTime())
            ? receipt.created_at : timestamp.toLocaleString()));
          if (receipt.status === "interrupted") {
            if (receipt.recovery_acknowledged_at) {
              item.append(el("span", "subtle", "Workspace review acknowledged. Verify before retrying."));
            } else {
              const acknowledge = el("button", "secondary receipt-recovery", "I inspected the workspace");
              acknowledge.type = "button";
              acknowledge.addEventListener("click", async () => {
                acknowledge.disabled = true;
                try {
                  await api("/api/receipts/" + encodeURIComponent(receipt.id) + "/acknowledge-recovery", {
                    method: "POST"
                  });
                  await loadActivity();
                } catch (error) {
                  acknowledge.disabled = false;
                  appendMessage("assistant", error.message, { kind: "error" });
                }
              });
              item.append(acknowledge);
              item.append(el("span", "subtle", "Inspect affected files before retrying. SOME does not auto-replay interrupted work."));
            }
          }
          activityList.append(item);
        });
      } catch (error) {
        activityList.replaceChildren(el("li", "error", error.message || "Could not load activity."));
      }
    }

    activityToggle.addEventListener("click", async () => {
      const opening = activityPanel.classList.contains("hidden");
      activityPanel.classList.toggle("hidden");
      activityToggle.setAttribute("aria-expanded", String(opening));
      if (opening) await loadActivity();
    });

    async function sendChat(message) {
      const text = message.trim();
      if (!text || activeRequests > 0) return;
      appendMessage("user", text);
      promptInput.value = "";
      resizeInput();
      beginRequest("Working on your request…");
      try {
        const result = await api("/api/chat", {
          method: "POST",
          body: JSON.stringify({ message: text })
        });
        appendMessage("assistant", result.message || "The action finished.", result, result.plan_id);
      } catch (error) {
        appendMessage("assistant", error.message || "Could not reach the local service.", { kind: "error" });
      } finally {
        endRequest();
        promptInput.focus();
      }
    }

    function resizeInput() {
      promptInput.style.height = "auto";
      promptInput.style.height = Math.min(promptInput.scrollHeight, 140) + "px";
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      sendChat(promptInput.value);
    });
    promptInput.addEventListener("input", resizeInput);
    promptInput.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    quickPromptButtons.forEach(button => {
      button.addEventListener("click", () => sendChat(button.getAttribute("data-prompt") || ""));
    });

    uploadInput.addEventListener("change", async () => {
      const file = uploadInput.files && uploadInput.files[0];
      if (!file) return;
      if (file.size > 10 * 1024 * 1024) {
        appendMessage("assistant", "Upload refused. Files are limited to 10 MiB.", { kind: "error" });
        uploadInput.value = "";
        return;
      }
      if (activeRequests > 0) {
        uploadInput.value = "";
        return;
      }
      appendMessage("user", "Upload: " + file.name + " (" + formatBytes(file.size) + ")");
      beginRequest("Saving file locally…");
      const formData = new FormData();
      formData.append("file", file);
      try {
        const response = await fetch("/api/upload", { method: "POST", body: formData });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "Upload failed.");
        appendMessage("assistant", result.message, {
          result: { action: "upload_file", file: result.file, size_bytes: result.size_bytes },
          receipt_id: result.receipt_id
        });
      } catch (error) {
        appendMessage("assistant", error.message, { kind: "error" });
      } finally {
        endRequest();
        uploadInput.value = "";
      }
    });

    fetch("/api/health").then(response => response.json()).then(state => {
      if (state.status === "ok") {
        document.getElementById("status-text").textContent =
          state.planner === "rules" ? "Local · rules mode" : "Local · model optional";
      }
    }).catch(() => {
      document.getElementById("status-text").textContent = "Local service";
    });
