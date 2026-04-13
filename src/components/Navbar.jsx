import React from 'react'
import { useAuth } from "../AuthContext";
import { useNavigate } from 'react-router-dom'

const Navbar = () => {
  const { setAccessToken, setUser, user } = useAuth();
  const navigate = useNavigate();

  const logoutAction = async (e) => {
    e.preventDefault();

    try {
      await fetch("/logout", {
        method: "POST",
        credentials: "include",
      });

      setUser(null);
      setAccessToken(null);

      navigate("/");
    } catch (err) {
      console.error(err);
    }
  }
  return (
    <div className="app-title">
        <span>📅 AI Calendar Assistant</span>
        <div className="user-info">
            <span>Welcome<strong>{user ? ", " + user.username : ""}</strong></span>
            <button onClick={logoutAction} id="logoutBtn">Logout</button>
        </div>
    </div>
  )
}

export default Navbar
