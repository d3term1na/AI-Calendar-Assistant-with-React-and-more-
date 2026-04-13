import { useCallback, useState } from 'react';
import Chatbox from "../components/Chatbox";
import Calendar from "../components/Calendar";
import { useAuthFetch } from '../AuthFetch';


const MainPage = () => {
    const authFetch = useAuthFetch();
    const [events, setEvents] = useState({});
    const fetchEvents = useCallback(async () => {
        try {
            const res = await authFetch("/events");
            console.log("URL response:", res.url);
  
            if (!res.ok) return;
  
            const data = await res.json();
  
            const grouped = {};
  
            (data.events || []).forEach(event => {
                const dateKey = event.start_time.split(" ")[0];
  
                if (!grouped[dateKey]) grouped[dateKey] = [];
                grouped[dateKey].push(event);
            });
  
            setEvents(grouped);
        } catch (err) {
            console.error("Error loading events:", err);
        }
    }, [authFetch]);
    return (
        <div className="app-container">
          <Chatbox fetchEvents={fetchEvents}/>
          <Calendar events={events} fetchEvents={fetchEvents}/>
        </div>
    )
}

export default MainPage
