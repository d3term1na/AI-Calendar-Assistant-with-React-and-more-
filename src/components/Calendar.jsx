import { useState, useEffect, useCallback } from 'react'

const Calendar = ({ events, fetchEvents }) => {
    const [currentMonth, setCurrentMonth] = useState(new Date().getMonth());
    const [currentYear, setCurrentYear] = useState(new Date().getFullYear());
    const [hoveredId, setHoveredId] = useState("")
    const monthNames = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ];

    const firstDay = new Date(currentYear, currentMonth, 1);
    const lastDay = new Date(currentYear, currentMonth + 1, 0);
    const startingDay = firstDay.getDay();
    const totalDays = lastDay.getDate();

    const today = new Date();
    const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

    const days = [];

    useEffect(() => {
        fetchEvents();
    }, [currentMonth, currentYear]);

    // Previous month days
    const prevMonthLastDay = new Date(currentYear, currentMonth, 0).getDate();
    for (let i = startingDay - 1; i >= 0; i--) {
        const day = prevMonthLastDay - i;
        days.push(
        <div key={`prev-${day}`} className="calendar-day other-month">
            <div className="day-number">{day}</div>
        </div>
        );
    }

    // Current month days
    for (let day = 1; day <= totalDays; day++) {
        const dateKey = `${currentYear}-${String(currentMonth + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
        const isToday = dateKey === todayKey;
        const dayEvents = events[dateKey] || [];

        const sortedEvents = [...dayEvents].sort((a, b) => {
            const timeA = a.start_time.split(" ")[1] || "00:00:00";
            const timeB = b.start_time.split(" ")[1] || "00:00:00";
            return timeA.localeCompare(timeB);
        });

        days.push(
            <div
                key={dateKey}
                className={`calendar-day ${isToday ? "today" : ""}`}
                data-date={dateKey}
            >
                <div className="day-number">{day}</div>

                <div className="day-events">
                    {sortedEvents.map((e) => {
                        const start_time = e.start_time.split(" ")[1]?.substring(0, 5) || "";
                        const end_time = e.end_time.split(" ")[1]?.substring(0, 5) || "";
                        return (
                            <div 
                                key={e.event_id} 
                                className="event-item" 
                                title={e.title}
                                onMouseEnter={() => setHoveredId(e.event_id)}
                                onMouseLeave={() => setHoveredId(null)}
                            >
                                {start_time} {e.title}
                                {hoveredId === e.event_id && (
                                    <div className="event-popup">
                                        <strong>{e.title}</strong>
                                        <div>{`${start_time} - ${end_time}`}</div>
                                        <div>{e.participants.join(', ')}</div>
                                        <br/>
                                        <div>{e.notes || "No details"}</div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>
        );
    }

    // Next month days
    const totalCellsUsed = startingDay + totalDays;
    const remainingInRow = (7 - (totalCellsUsed % 7)) % 7;

    for (let day = 1; day <= remainingInRow; day++) {
        days.push(
            <div key={`next-${day}`} className="calendar-day other-month">
                <div className="day-number">{day}</div>
            </div>
        );
    }

    const handlePrev = () => {
        if (currentMonth === 0) {
            setCurrentMonth(11);
            setCurrentYear(prev => prev - 1);
        } else {
            setCurrentMonth(prev => prev - 1);
        }
    };

    const handleNext = () => {
        if (currentMonth === 11) {
            setCurrentMonth(0);
            setCurrentYear(prev => prev + 1);
        } else {
            setCurrentMonth(prev => prev + 1);
        }
    };

    const handleToday = () => {
        const today = new Date();
        setCurrentMonth(today.getMonth());
        setCurrentYear(today.getFullYear());
    };


    return (
        <div className="calendar-panel">
            <div className="calendar-header">
                <h2>
                    {monthNames[currentMonth]} {currentYear}
                </h2>

                <div className="calendar-nav">
                    <button onClick={handlePrev}>← Prev</button>
                    <button onClick={handleToday}>Today</button>
                    <button onClick={handleNext}>Next →</button>
                </div>
            </div>

            <div className="calendar">
                <div className="calendar-weekdays">
                    {["Sun","Mon","Tue","Wed","Thu","Fri","Sat"].map(day => (
                        <div key={day}>{day}</div>
                    ))}
                </div>

                <div className="calendar-days">
                    {days}
                </div>
            </div>
        </div>
    )
}

export default Calendar
