import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from "../AuthContext";

const LoginPage = () => {
    const [auth, setAuth] = useState("Login");
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const { setAccessToken, setUser } = useAuth();
    const navigate = useNavigate();

    const login = async (user) => {
        const res = await fetch("/api/users/token", {
            method: "POST",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body: new URLSearchParams(user),
            credentials: "include",
        });

        if (!res.ok) throw new Error("Login failed");

        const data = await res.json();

        setAccessToken(data.access_token);

        const meRes = await fetch("/me", {
            headers: {
                Authorization: `Bearer ${data.access_token}`,
            },
        });

        const me = await meRes.json();
        setUser(me);
        setUsername("");
        setPassword("");
        navigate("/main");
    };

    const register = async (newUser) => {
        const res = await fetch("/register", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(newUser),
            credentials: "include",
        });

        if (!res.ok) throw new Error("Register failed");

        const data = await res.json();

        setAccessToken(data.access_token);

        const meRes = await fetch("/me", {
            headers: {
                Authorization: `Bearer ${data.access_token}`,
            },
        });

        const me = await meRes.json();
        setUser(me);
        setUsername("");
        setPassword("");
        navigate("/main");
    };

    const submitForm = async (e) => {
        e.preventDefault();
        setError("");
        setLoading(true);
        try{
            const user = {
                username,
                password
            }
            if (auth === "Login"){
                await login(user);
            } else {
                await register(user);
            }
        }catch (err){
            setError("Invalid username or password");
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="auth-wrapper">
            <div className="auth-container">
                <h1>📅 AI Calendar</h1>
                <p className="subtitle">Your intelligent scheduling assistant</p>

                <div className="auth-tabs">
                    <button 
                        className={`auth-tab ${auth === "Login" ? "active" : ""}`} 
                        data-tab="login" 
                        onClick={() => {
                            setAuth("Login");
                            setUsername("");
                            setPassword("");
                        }}>
                        Login
                    </button>
                    <button 
                        className={`auth-tab ${auth === "Register" ? "active" : ""}`}
                        data-tab="register" 
                        onClick={() => {
                            setAuth("Register");
                            setUsername("");
                            setPassword("");
                        }}>
                        Register
                    </button>
                </div>

                {error && <div className="auth-error">{error}</div>}
                <form onSubmit={submitForm} className="auth-form">
                    <input 
                        type="text" 
                        placeholder={ auth === "Login"  ? "Username" : "Username (min 3 chars)" }
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        required/>
                    <input 
                        type="password" 
                        placeholder={ auth === "Login"  ? "Password" : "Password (min 4 chars)"}
                        value={password}
                        onChange={(e) => setPassword(e.target.value)} 
                        required/>
                    <button type="submit" disabled={loading}>{loading ? "Please wait..." : auth === "Login" ? "Login" : "Create Account"}</button>
                </form>
            </div>
        </div>
    )
}

export default LoginPage
