import React from 'react';
import { Outlet } from 'react-router-dom';
import NavBar from '../components/Navbar';

const MainLayout = (logout) => {
    return (
        <div className="app-wrapper">
            <NavBar user="Daniel" logout={logout}/>
            <Outlet/>
        </div>
    )
}

export default MainLayout
