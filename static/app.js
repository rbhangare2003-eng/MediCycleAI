let mediaStream = null;

function startCamera() {
    const video = document.getElementById("cameraStream");
    if (!video) return;

    navigator.mediaDevices.getUserMedia({ video: true })
        .then(stream => {
            mediaStream = stream;
            video.srcObject = stream;
            video.style.display = "block";
        })
        .catch(err => {
            alert("Camera access failed: " + err.message);
        });
}

function capturePhoto() {
    const video = document.getElementById("cameraStream");
    const canvas = document.getElementById("cameraCanvas");
    const cameraData = document.getElementById("cameraData");
    if (!video || !canvas || !cameraData) return;

    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg");
    cameraData.value = dataUrl;
    canvas.style.display = "block";

    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        video.style.display = "none";
    }
}

function toggleChatbot() {
    const box = document.getElementById("chatbotBox");
    if (!box) return;
    box.classList.toggle("open");
}

async function sendChat() {
    const input = document.getElementById("chatInput");
    const messages = document.getElementById("chatMessages");
    if (!input || !messages) return;

    const text = input.value.trim();
    if (!text) return;

    messages.innerHTML += `<div class="user-msg">${text}</div>`;
    input.value = "";

    const res = await fetch("/chatbot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text })
    });

    const data = await res.json();
    messages.innerHTML += `<div class="bot-msg">${data.reply}</div>`;
    messages.scrollTop = messages.scrollHeight;

    if ("speechSynthesis" in window) {
        const utter = new SpeechSynthesisUtterance(data.reply);
        speechSynthesis.speak(utter);
    }
}

function startVoiceInput() {
    const input = document.getElementById("chatInput");
    if (!input) return;

    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
        alert("Voice recognition is not supported in this browser.");
        return;
    }

    const recognition = new Recognition();
    recognition.lang = "en-US";
    recognition.start();

    recognition.onresult = function(event) {
        input.value = event.results[0][0].transcript;
    };
}
