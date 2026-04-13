import { useState, useEffect, useRef } from 'react'
import { useAuthFetch } from '../AuthFetch';

const Chatbox = ({ fetchEvents }) => {
    const authFetch = useAuthFetch();

    const [suggestions, setSuggestions] = useState([]);
    const [dismissed, setDismissed] = useState(new Set());
    const [insights, setInsights] = useState("");
    const [input, setInput] = useState("");
    const [messages, setMessages] = useState([]);
    const [loading, setLoading] = useState(false);

    const fetchAgendaSuggestions = async () => {
        try {
            const res = await authFetch('/agenda-suggestions');
            if (!res.ok) return;

            const data = await res.json();
            setSuggestions(data.suggestions || []);
        } catch (err) {
            console.error('Error fetching agenda suggestions:', err);
        }
    };

    const currentSuggestion = suggestions.find(
        s => !dismissed.has(s.event_id)
    );

    const formatTime = (time) => {
        try {
            const date = new Date(time.replace(" ", "T"));
            return date.toLocaleDateString("en-US", {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
                hour: 'numeric',
                minute: '2-digit'
            });
        } catch {
            return time;
        }
    };

    const dismissSuggestion = (id) => {
        setDismissed(prev => new Set([...prev, id]));
    };

    const useSuggestion = (suggestion) => {
        setInput(suggestion.suggested_agenda);
        dismissSuggestion(suggestion.event_id);
    };

    const fetchSchedulingInsight = async () => {
        try {
            const res = await authFetch('/scheduling-insights');
            if (res.ok) {
                const data = await res.json();
                if (data.insight) {
                    setInsights(data.insight)
                }
            }
        } catch (err) {
            console.error('Error fetching scheduling insight:', err);
        }
    }

    useEffect(() => {
        fetchAgendaSuggestions();
        fetchSchedulingInsight();
    }, []);

    const handleSend = async () => {
        const msg = input.trim();
        if (!msg || loading) return;

        // 1. add user message
        setMessages(prev => [...prev, { role: "user", text: msg }]);

        setInput("");
        setLoading(true);

        // 2. add loading bubble
        setMessages(prev => [...prev, { role: "agent", text: "Thinking..." }]);

        try {
            const conversationId = "default"
            const res = await authFetch("/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    conversation_id: conversationId,
                    message: msg
                })
            });

            if (!res.ok) {
                setMessages(prev => [
                    ...prev.slice(0, -1),
                    { role: "agent", text: `Error: ${res.status}` }
                ]);
                return;
            }

            const data = await res.json();

            // replace "Thinking..." bubble with real response
            setMessages(prev => [
                ...prev.slice(0, -1),
                { role: "agent", text: data.reply || "No reply from server" }
            ]);

            // Only update calendar state if needed:
            if (data.metadata) {
                if (
                    data.metadata.events_created ||
                    data.metadata.events_updated ||
                    data.metadata.events_deleted
                ) {
                    fetchEvents(); // refresh calendar only
                }
            }

        } catch (err) {
            setMessages(prev => [
                ...prev.slice(0, -1),
                { role: "agent", text: "Error: could not reach backend" }
            ]);
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const chatRef = useRef(null);

    useEffect(() => {
        const el = chatRef.current;
        if (!el) return;

        const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;

        if (isNearBottom) {
            el.scrollTo({
                top: el.scrollHeight,
                behavior: "smooth"
            });
        }
    }, [messages]);

    return (
        <div className="left-panel">
            <div className="chat-container">
                <div id="chat" ref={chatRef}>
                    {messages.map((m, i) => (
                        <div
                            key={i}
                            className={`message ${m.role}`}
                        >
                            {m.text}
                        </div>
                    ))}
                </div>
                {currentSuggestion && (
                    <div className="suggestion-box">
                        <div className="suggestion-box-header">
                            <span className="suggestion-box-title">
                                Suggested Agenda Items
                            </span>

                            <button
                                className="suggestion-box-close"
                                onClick={() => dismissSuggestion(currentSuggestion.event_id)}
                            >
                                ×
                            </button>
                        </div>

                        <div className="suggestion-box-meeting">
                            For: {currentSuggestion.event_title} (
                                {formatTime(currentSuggestion.event_time)}
                            )
                        </div>

                        <div className="suggestion-box-content">
                            {currentSuggestion.suggested_agenda}
                        </div>

                        <button
                            className="suggestion-box-use"
                            onClick={() => useSuggestion(currentSuggestion)}
                        >
                            Use as message
                        </button>
                    </div>
                )}
                <div id="inputArea">
                    <div className="input-wrapper">
                        <input
                            id="message"
                            className="message-input"
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                    handleSend();
                                }
                            }}
                            placeholder={insights ? insights : "Type a message..."}
                        />
                    </div>
                    <button id="send" onClick={handleSend} disabled={loading}>Send</button>
                </div>
            </div>
        </div>
    )
}

export default Chatbox
