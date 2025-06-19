import React from 'react';
import { menu_list } from '../assets/assets';

const ExploreMenu = ({category, setCategory}) => {
  return (
    <div className='flex flex-col gap-[20px]' id='menu'>
        <h1 className='font-bold text-xl md:text-2xl lg:text-3xl'>Explore Our Menu</h1>
        <p className='w-full md:max-w-[80%] lg:max-w-[60%] text-sm md:text-base lg:text-xl'>Choose from diverse menu featuring a delectable array of dishes. Our mission is to satisfy your cravings and elivate your dining experience, one delicious meal at a time.</p>
        <div className='flex justify-between items-center text-center gap-[20px] md:gap-[30px] lg:gap-[40px] my-20px mx-0px overflow-x-scroll'>
            {menu_list.map((item, index) => {
                return (
                    <div onClick={() => setCategory(prev => prev === item.menu_name ? 'All' : item.menu_name)} key={index}>
                        <img src={item.menu_image} className={`w-7.5vw md:w-14vw lg:w-20vw h-7.5vw md:h-14vw lg:h-20vw min-w-[80px] md:min-w-[150px] lg:min-w-[200px] cursor-pointer rounded-full ${category === item.menu_name ? 'border-[4px] border-solid border-orange-600 p-[2px]' : ''}`}/>
                        <p className='mt-[10px] text-[#747474] text-sm md:text-base lg:text-xl'>{item.menu_name}</p>
                    </div>
                )
            })}
        </div>
        <hr className='my-[5px] md:my-[7px] lg:my-[10px] mx-[0px] h-[2px] bg-[#e2e2e2] border-none' />
    </div>
  )
}

export default ExploreMenu