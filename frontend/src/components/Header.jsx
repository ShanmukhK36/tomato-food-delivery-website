import React from 'react';
import headerImg from '../assets/header_img.png';
import '../index.css';

const Header = () => {
  return (
    <div className='h-[51vw] sm:h-[34vw] my-[30px] mx-auto bg-no-repeat bg-cover relative bg-contain rounded-md' style={{backgroundImage: `url(${headerImg})`}}>
        <div className='absolute flex flex-col items-start gap-[1.5vw] max-w-[50%] bottom-[10%] left-[6vw] fade-in'>
            <h2 className='font-medium text-white text-[14px] md:text-[30px] lg:text-[50px]'>Order your favourite food here</h2>
            <p className='text-white text-[10px] md:text-[16px] lg:text-[22px]'>Choose from diverse menu featuring a delectable array of dishes crafted with the finest ingredients and culinary expertise. Our mission is to satisfy your cravings and elivate your dining experience, one delicious meal at a time.</p>
            <button onClick={() => window.location.href = '#menu'} className='w-20 md:w-30 lg:w-50 h-5 md:h-10 lg:h-15 rounded-full bg-white text-black text-[10px] md:text-[16px] lg:text-[22px] cursor-pointer'>View Menu</button>
        </div>
    </div>
  )
}

export default Header;