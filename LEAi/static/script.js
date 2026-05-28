let sessionId = "";

// Show the main menu modal on page load
window.onload = function () {
    document.getElementById("mainMenuModal").classList.remove("hidden");
    document.getElementById("personalityModal").classList.add("hidden");
    document.getElementById("chatContainer").classList.add("hidden");
};

// New Conversation
document.getElementById("newConversationBtn").addEventListener("click", () => {
    document.getElementById("mainMenuModal").classList.add("hidden");
    document.getElementById("personalityModal").classList.remove("hidden");
});

// Continue Conversation (now **only** loads existing session, does NOT call `/start`)
document.getElementById("continueConversationBtn").addEventListener("click", () => {
    document.getElementById("mainMenuModal").classList.add("hidden");
    const id = prompt("Enter your previous session (personality) ID:");
    if (id) {
        sessionId = id;
        document.getElementById("sessionId").textContent = sessionId;
        document.getElementById("personalityModal").classList.add("hidden");
        document.getElementById("chatContainer").classList.remove("hidden");
        // Now fetch and display previous messages
        fetch("/load_session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ persona_id: sessionId })
        })
        .then(res => res.json())
        .then(data => {
            const chatBox = document.getElementById("chatBox");
            chatBox.innerHTML = "";
            if (data.history) {
                data.history.forEach(msg => addMessage(msg.role, msg.text));
            }
        })
        .catch(err => {
            alert("Failed to load session: " + err.message);
            document.getElementById("mainMenuModal").classList.remove("hidden");
            document.getElementById("chatContainer").classList.add("hidden");
        });
    } else {
        document.getElementById("mainMenuModal").classList.remove("hidden");
    }
});

// Handle personality choice buttons (new chat flow)
function handlePersonalityChoice(useCustom, existingPersonaId = null) {
    document.getElementById("personalityModal").classList.add("hidden");
    document.getElementById("chatContainer").classList.remove("hidden");

    const body = { custom: useCustom };
    if (existingPersonaId) {
        body.persona_id = existingPersonaId;
    }

    fetch("/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    })
    .then(res => res.json())
    .then(data => {
        sessionId = data.persona_id;
        document.getElementById("sessionId").textContent = sessionId;
        if (data.initial_prompt) {
            addMessage("LEAi", data.initial_prompt);
        }
    })
    .catch(err => {
        alert("Error starting new conversation: " + err.message);
        document.getElementById("mainMenuModal").classList.remove("hidden");
        document.getElementById("chatContainer").classList.add("hidden");
    });
}

document.getElementById("defaultBtn").addEventListener("click", () => handlePersonalityChoice(false));
document.getElementById("customBtn").addEventListener("click", () => handlePersonalityChoice(true));

document.getElementById("optionsBtn").addEventListener("click", () => {
    document.getElementById("dropdown").classList.toggle("hidden");
});

function copySessionId() {
    if (!sessionId) {
        alert("No session ID to copy!");
        return;
    }
    navigator.clipboard.writeText(sessionId);
    alert("Session ID copied!");
}

function addMessage(role, text) {
    const chatBox = document.getElementById("chatBox");
    const messageWrapper = document.createElement("div");
    messageWrapper.classList.add("flex", "items-start", "gap-3", "mb-4");

    if (role === "LEAi") {
        messageWrapper.innerHTML = `
            <img src="/static/LEAi.png" alt="LEAi Avatar" class="w-14 h-14 rounded-full border border-white mt-1" />
            <div class="bg-pink-600 p-3 rounded-xl max-w-[80%] text-white">
                ${text}
            </div>
        `;
    } else {
        // Right side bubble, but text inside is left-aligned
        messageWrapper.innerHTML = `
            <div class="ml-auto max-w-[80%]">
                <div class="bg-blue-600 p-3 rounded-xl text-white text-left">
                    ${text}
                </div>
            </div>
        `;
    }

    chatBox.appendChild(messageWrapper);
    chatBox.scrollTop = chatBox.scrollHeight;
}


// --- MAIN CHANGE: Prevent sending messages if session not set ---
document.getElementById("chatForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = document.getElementById("messageInput");
    const message = input.value.trim();
    if (!message) return;

    // Block sending if session not set!
    if (!sessionId) {
        alert("Please start or load a conversation first!");
        return;
    }

    input.value = "";
    addMessage("You", message);

    fetch("/chat", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ persona_id: sessionId, message })
    })
    .then(res => res.json())
    .then(data => {
        if (data.setup_complete) {
            document.getElementById("chatBox").innerHTML = "";
            return;
        }

        if (data.reply) {
            addMessage("LEAi", data.reply);
        }
        if (data.error) {
            addMessage("LEAi", "Sorry, there was a problem: " + data.error);
        }
    })
    .catch(err => {
        addMessage("LEAi", "Network error: " + err.message);
    });
});
