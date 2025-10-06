import { assets } from '../assets/assets'
import { Link } from 'react-router-dom';

const Footer = () => {
    const admin_url = import.meta.env.VITE_ADMIN_URL;
  return (
    <div className='text-[#d9d9d9] bg-[#323232] flex flex-col items-center gap-[20px] py-[20px] px-[8vw] pt-[80px] mt-10' id='contact'>
        <div className='w-full sm:grid grid-cols-[2fr_1fr_1fr] sm:gap-[80px]'>
            <div className='flex flex-col items-start gap-[20px] mb-[30px]'>
                <img src={assets.logo}/>
                <p>Lorem Ipsum is simply dummy text of the printing and typesetting industry. Lorem Ipsum has been the industry's standard dummy text ever since the 1500s, when an unknown printer took a galley of type and scrambled it to make a type specimen book.</p>
                <div className='flex gap-[20px]'>
                    <img className='w-[40px] mr-[15px]' src={assets.facebook_icon}/>
                    <img className='w-[40px] mr-[15px]' src={assets.twitter_icon}/>
                    <img className='w-[40px] mr-[15px]' src={assets.linkedin_icon}/>
                </div>
            </div>
            <div className='flex flex-col items-start gap-[20px] mb-[30px]'>
                <h2 className='text-[20px] font-[500] text-white'>COMPANY</h2>
                <ul >
                    <li>Home</li>
                    <li>About Us</li>
                    <li>Delivery</li>
                    <li>Privacy Policy</li>
                </ul>
            </div>
            <div className='flex flex-col items-start gap-[20px] mb-[30px]'>
                <h2 className='text-[20px] font-[500] text-white'>GET IN TOUCH</h2>
                <ul>
                    <li>+1-100-200-300</li>
                    <li>contact@tomato.com</li>
                    <Link to={admin_url}>Admin Dashboard</Link>
                </ul>
            </div>
        </div>
        <hr className='w-full h-[2px] my-[20px] mx-[0px] bg-grey border border-[1px]'></hr>
        <p>Copyright 2025 @ Tomato.com - All Rights Reserved.</p>
    </div>
  )
}

export default Footer;