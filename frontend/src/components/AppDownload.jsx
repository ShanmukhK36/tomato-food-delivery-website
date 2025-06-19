import React from 'react'
import { assets } from '../assets/assets';

const AppDownload = () => {
  return (
    <div className='my-auto mx-auto mt-[100px] text-center text-[25px] md:text-[30px] lg:text-[35px] font-[500]' id='mobile'>
        <p>For Better Experience Download <br />Tomato App</p>
        <div className='flex justify-center mt-[40px] gap-[10px]'>
            <img className='w-[100px] md:w-[140px] lg:w-[180px] transition-3s cursor-pointer hover:scale-105' src={assets.play_store} />
            <img className='w-[100px] md:w-[140px] lg:w-[180px] transition-3s cursor-pointer hover:scale-105' src={assets.app_store} />
        </div>
    </div>
  )
}

export default AppDownload;