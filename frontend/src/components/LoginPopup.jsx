import { useContext, useEffect, useState } from 'react';
import '../index.css';
import { RxCross1 } from "react-icons/rx";
import { StoreContext } from '../context/StoreContext';
import axios from 'axios';

const LoginPopup = ({setShowLogin}) => {
    const {url, setToken} = useContext(StoreContext);
    const [currentState, setCurrentState] = useState('Sign Up');
    const [data, setData] = useState({
        name: '',
        email: '',
        password: ''
    });
    const onChangeHandler = (event) => {
        const name = event.target.name;
        const value = event.target.value;
        setData(data => ({...data, [name]: value}))
    }
    const onLogin = async (event) => {
        event.preventDefault();
        let newUrl = url;
        if(currentState === 'Login') {
            newUrl += '/api/user/login';
        } else {
            newUrl += '/api/user/register';
        }
        const response = await axios.post(newUrl, data);
        if(response.data.success) {
            const {token, user} = response.data;
            setToken(token);
            localStorage.setItem('token', token);
            if (user?._id) {
                localStorage.setItem('userId', user._id);
            }
            setShowLogin(false);
        } else {
            alert(response.data.message);
        }
    }
    useEffect(() => {
        document.body.style.overflow = 'hidden'; // lock scroll
        return () => {
            document.body.style.overflow = 'auto'; // restore on unmount
        };
    }, []);
  return (
    <div className='absolute justify-center w-full h-full z-1 bg-[#00000090] grid'>
        <form onSubmit={onLogin} className='place-self-center flex flex-col w-[330px] md:w-[400px] lg:w-[500px] gap-[25px] text-[#808080] bg-white py-[25px] px-[30px] text-[14px] fade-in-login rounded-[8px]'>
            <div className='flex justify-between text-black'>
                <h2 className='text-bold font-[500] text-[22px]'>{currentState}</h2>
                <RxCross1 className='border border-gray-700 p-[2px] w-[25px] h-[25px] text-black hover:text-white hover:bg-red-600' onClick={() => setShowLogin(false)} />
            </div>
            <div className='flex flex-col gap-[25px]'>
                {currentState === 'Login' ? <></> : <input name='name' onChange={onChangeHandler} value={data.name} className='outline-none border-solid border-[1px] border-[#c9c9c9] p-[10px] rounded-[4px] bg-gray-50 text-[15px]' type='text' placeholder='Enter Name' required />}
                <input name='email' onChange={onChangeHandler} value={data.email} className='outline-none border-solid border-[1px] border-[#c9c9c9] p-[10px] rounded-[4px] bg-gray-50 text-[15px]' type='email' placeholder='Enter Email' required />
                <input name='password' onChange={onChangeHandler} value={data.password} className='outline-none border-solid border-[1px] border-[#c9c9c9] p-[10px] rounded-[4px] bg-gray-50 text-[15px]' type='password' placeholder='Enter Password' required />
            </div>
            <button type='submit' className='border-none rounded-[4px] border-orange-600 bg-orange-600 hover:bg-orange-700 p-2 text-white cursor-pointer'>{currentState === 'Sign Up' ? 'Create Account' : 'Login'}</button>
            <div className='flex items-start gap-[10px] mt-[-15px]'>
                <input className='mt-[5px]' type='checkbox' required/>
                <p>By continuing, I agree to the terms of use & privacy policy.</p>
            </div>
            {currentState === 'Login' ? <p>create an account? <span className='cursor-pointer text-orange-600 no-underline hover:underline' onClick={() => setCurrentState('Sign Up')}>click here</span></p> : <p>already have an account? <span className='cursor-pointer text-orange-600 no-underline hover:underline' onClick={() => setCurrentState('Login')}>login here</span></p>}
        </form>
    </div>
  )
}

export default LoginPopup;