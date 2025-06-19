import '../index.css';
import { assets } from '../assets/assets';
import { Link, useNavigate } from 'react-router-dom';
import { useContext, useEffect, useRef, useState } from 'react';
import { StoreContext } from '../context/StoreContext';

const Navbar = ({setShowLogin}) => {
    const [profileVisible, setProfileVisible] = useState(false);
    const [menu, setMenu] = useState('home');
    const {token, setToken, getTotalCartValue, setShowSearch} = useContext(StoreContext);
    const dropdownRef = useRef(null);
    const navigate = useNavigate();
    const logout = () => {
        localStorage.removeItem('token');
        setToken('');
        navigate('/');
    }
    const handleSearch = () => {
        setShowSearch(true)
        navigate('/')
    }
    useEffect(() => {
        const handleClickOutside = (event) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
                setProfileVisible(false);
            }
        };

        if (profileVisible) {
            document.addEventListener('mousedown', handleClickOutside);
        }

        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, [profileVisible]);
  return (
    <div className='w-full pl-2 pr-2 h-20 py-5 flex items-center justify-between'>
        <Link to='/'>
            <img onClick={() => setMenu('home')} src={assets.logo} className='w-[150px]'/>
        </Link>
        <ul className='hidden sm:flex gap-[20px] outfit-display text-[#49557e] font-[18px]'>
            <Link to='/' onClick={() => setMenu('home')} className='flex flex-col items-center gap-1 cursor-pointer'>
                <p>Home</p>
                <hr className={menu === 'home' ? 'w-2/4 border-none h-[1.6px] bg-[#49557e] block' : ''}></hr>
            </Link>
            <a href='#menu' onClick={() => setMenu('menu')} className='flex flex-col items-center gap-1 cursor-pointer'>
                <p>Menu</p>
                <hr className={menu === 'menu' ? 'w-2/4 border-none h-[1.6px] bg-[#49557e] block' : ''}></hr>
            </a>
            <a href='#mobile' onClick={() => setMenu('mobile-app')} className='flex flex-col items-center gap-1 cursor-pointer'>
                <p>Mobile-App</p>
                <hr className={menu === 'mobile-app' ? 'w-2/4 border-none h-[1.6px] bg-[#49557e] block' : ''}></hr>
            </a>
            <a href='#contact' onClick={() => setMenu('contact-us')} className='flex flex-col items-center gap-1 cursor-pointer'>
                <p>Contact Us</p>
                <hr className={menu === 'contact-us' ? 'w-2/4 border-none h-[1.6px] bg-[#49557e] block' : ''}></hr>
            </a>
        </ul>
        <div className='flex items-center gap-[10px] md:gap-[20px] lg:gap-[40px]'>
            <img onClick={handleSearch} className='w-5 h-5 mt-7 mb-7 position-absolute min-w-[10px] min-h-[10px] cursor-pointer' src={assets.search_icon}/>
            <Link to='/cart' className='relative'>
                <img className='w-5 h-5 mt-7 mb-7 position-relative' src={assets.basket_icon}/>
                {getTotalCartValue() === 0 ? <></> : 
                    <p className='absolute right-[-2px] top-[22px] w-2 h-2 leading-4 rounded-full bg-orange-600'></p>
                }
            </Link>
            {!token ? <button className='justify-content text-white px-5 py-2 sm:px-7 sm:py-2 rounded-full text-xs sm:text-sm bg-orange-600 hover:bg-orange-700 cursor-pointer' onClick={() => setShowLogin(true)}>Sign In</button>
            : <div className='relative group'>
                <img onClick={() => setProfileVisible(!profileVisible)} className='w-5 h-5 mt-7 mb-7 position-relative' src={assets.profile_icon}/>
                {profileVisible &&
                <ul className='absolute w-[150px] top-13 right-0 z-1 display-none flex flex-col gap-[10px] bg-[#fff2ef] py-[12px] px-[25px] rounded-[4px] border border-orange-600 outline-[2px] outline-white' ref={dropdownRef}>
                    <li onClick={() => navigate('/myorders')} className='flex items-center gap-[10px] cursor-pointer text-sm hover:text-orange-600'><img className='w-[20px]' src={assets.bag_icon}/>Orders</li>
                    <hr className='w-full border-none h-[1.6px] bg-[#49557e] block'/>
                    <li onClick={logout} className='flex items-center gap-[10px] cursor-pointer text-sm hover:text-orange-600'><img className='w-[20px]' src={assets.logout_icon}/>Logout</li>
                </ul>}
            </div>}
        </div>
    </div>
  )
}

export default Navbar;