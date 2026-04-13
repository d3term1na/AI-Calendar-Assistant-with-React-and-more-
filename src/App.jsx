import { Route, createBrowserRouter, createRoutesFromElements, RouterProvider, } from 'react-router-dom'
import MainLayout from './layouts/MainLayout';
import MainPage from './pages/MainPage';
import LoginPage from './pages/LoginPage';
import NotFoundPage from './pages/NotFoundPage';
import ProtectedRoute from "./components/ProtectedRoute";


const App = () => {
  // const useAuthFetch = () => {
  //   const { accessToken, setAccessToken } = useAuth();

  //   const authFetch = async (url, options = {}) => {
  //     let res = await fetch(url, {
  //       ...options,
  //       headers: {
  //         ...options.headers,
  //         Authorization: `Bearer ${accessToken}`
  //       }
  //     });

  //     if (res.status === 401) {
  //       // try refresh
  //       const newToken = await refresh();

  //       if (!newToken) {
  //         // logout user
  //         setAccessToken(null);
  //         return res;
  //       }

  //       setAccessToken(newToken);

  //       // retry request
  //       res = await fetch(url, {
  //         ...options,
  //         headers: {
  //           ...options.headers,
  //           Authorization: `Bearer ${newToken}`
  //         }
  //       });
  //     }

  //     return res;
  //   };
  //   return authFetch;
  // };

  const router = createBrowserRouter(
    createRoutesFromElements(
      <Route path='/' element={<MainLayout/>}>
        <Route index element={<LoginPage/> }/>
        <Route path='/main' element={
          <ProtectedRoute>
            <MainPage />
          </ProtectedRoute>
        }/>
        <Route path='*' element={<NotFoundPage/>}/>
      </Route>
    )
  );
  return <RouterProvider router={router}/>;
}

export default App
