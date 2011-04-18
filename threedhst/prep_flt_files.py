"""
3DHST.prep_flt_files

Process RAW flt files to 

    1) Subtract background
    
    2) Align to reference (e.g. ACS-z)
    
    3) Combine full direct mosaics and mosaics of grism pointings with 
       like orientation angles.
    
"""

__version__ = "$Rev$"
# $URL$
# $Author$
# $Date$

import os
import glob
import shutil

import numpy as np
import pyfits
import scipy.linalg
# from scipy import polyfit, polyval
import matplotlib.pyplot as plt

from pyraf import iraf
from iraf import stsdas,dither,slitless,axe 

INDEF = iraf.INDEF
no = iraf.no
yes = iraf.yes

import threedhst

class fit_2D_background():
    
    def __init__(self, ORDER=-1, x0=None, y0=None, DQMAX=10,
        IMAGES=['/research/HST/GRISM/CONF/G141_sky_cleaned.fits']):
        """
__init__(self, ORDER=-1, x0=None, y0=None, DQMAX=10,
         IMAGES=['/research/HST/GRISM/CONF/G141_sky_cleaned.fits'])
    
    ORDER: Polynomial order of the fit, e.g.
        ORDER=2 - 0th order, x, y, x**2, y**2, x*y
        ORDER=3 - 0th order, x, y, x**2, y**2, x*y, x**2 y, x y**2, x**3, y**3
           
    x0, y0: reference coordinate for polynomical fit.  Defaults to image center
    
    DQMAX: Maximum value in FLT[DQ] extension considered OK
    
    IMAGES: Include actual images in the model that is fit.  This can be multiple files, such as the various background images for different sky levels.
    
        """
        self.ORDER = ORDER
        self.x0 = x0
        self.y0 = y0
        self.DQMAX = DQMAX
        self.IMAGES = IMAGES
        self.setup_matrices()
            
    def setup_matrices(self):
        """
setup_matrices()
    
    Setup self.A matrix for polynomial fit.
        """
        NX = 1014
        NY = 1014
        
        #### Image matrix indices
        xi,yi = np.indices((NX,NY))
        
        #### Default reference position is image center
        if self.x0 is None:
            self.x0 = NX/2.
        if self.y0 is None:
            self.y0 = NY/2.
                
        xi = (xi-self.x0*1.)/NX
        yi = (yi-self.y0*1.)/NY
        
        NPARAM  = np.sum(np.arange(self.ORDER+2)) #+1 #+1
        NPARAM += len(self.IMAGES)
        self.NPARAM = NPARAM
        
        self.A = np.zeros((NPARAM,NX,NY))
        
        #### Read images to add to the "model"
        count=0
        for img in self.IMAGES:
            hdu = pyfits.open(img)
            self.A[count,:,: ] = hdu[0].data
            hdu.close()
            count += 1
        
        #### zeroth order, flat background
        if self.ORDER >= 0:
            self.A[count,:,:] += 1
            count+=1
        
        for pow in range(1,self.ORDER+1):
            pi = pow-1

            #### Cross terms
            while (pi > pow/2.):
                self.A[count,:,:] = xi**pi*yi**(pow-pi)
                print 'A[%d,:,:] = xi**%d*yi**%d' %(count,pi,pow-pi)
                count+=1
                self.A[count,:,:] = xi**(pow-pi)*yi**pi
                print 'A[%d,:,:] = xi**%d*yi**%d' %(count,pow-pi,pi)
                count+=1
                pi-=1
            
            #### x**pow/2 * y**pow/2 term
            if (pow/2. == np.int(pow/2.)):
                self.A[count,:,:] = xi**(pow/2)*yi**(pow/2)
                print 'A[%d,:,:] = xi**%d*yi**%d' %(count,pow/2,pow/2)
                count+=1
            
            #### x**pow, y**pow terms
            print 'A[%d,:,:] = xi**%d' %(count,pow)
            self.A[count,:,:] = xi**pow
            count+=1
            print 'A[%d,:,:] = yi**%d' %(count,pow)
            self.A[count,:,:] = yi**pow
            count+=1
        
        # #### Oth order for `grism` is True is G141 median image
        # #medF = pyfits.open('../CONF/WFC3.IR.G141.sky.V1.0.fits') # from aXe web
        # # cleaned of value=0 pixels
        # medF = pyfits.open(GRISM_SKY)
        # med_g141 = medF[0].data
        # medF.close()
        # 
        # self.B = self.A*1.
        # self.B[0,:,:] = med_g141*1.
        # self.NPARAM = NPARAM
    
    def fit_image(self, root, A=None, overwrite=False, show=True,
                  save_fit=False):
        """
fit_image(self, root, A=None, overwrite=False, show=True)
    
    Fit and optionally subtract the background from a FLT image.
    
    `root` is like "ib3728d2q"
    
    `A` is a matrix computed by `setup_matrices` or `__init__`.
    
    if `overwrite` is True:
        Write the background-subtracted image to the original FLT file
        
    if `show` is True:
        Show a plot of the original, background, and bg-subtracted images.
        
        """
        import os
        import glob
        
        if A is None:
            A = self.A
        
        #### Read the FLT file and get dimensions 
        #### (should always be 1014x1014 for WFC3)
        fi = pyfits.open(root+'_flt.fits',mode='update')
        IMG = fi[1].data
        DQ = fi[3].data
        NX,NY = IMG.shape
        
        #### array indices for each pixel position
        xi,yi = np.indices((NX,NY))
        xi = (xi-NX)/2./NX
        yi = (yi-NY)/2./NY
        
        #### If a segmentation image is available, read it
        #### for use as an object mask.
        segfile = glob.glob(root+'_flt.seg.fits*')
        if len(segfile) == 0:
            seg = IMG*0.
        else:
            print 'Segmentation image: %s' %segfile[0]
            fis = pyfits.open(segfile[0])
            seg = fis[0].data
        
        #### Apply segmentation mask, also mask out extreme IMG values 
        #### and any pixel with DQ flag > self.DQMAX
        
        q = np.where((seg == 0) & (IMG > -1) & (IMG < 4) & (DQ < self.DQMAX)) 
        qb = np.where((seg > 0) | (IMG < -1) | (IMG > 4) | (DQ >= self.DQMAX))
        IMGb = IMG*1.
        IMGb[qb] = np.nan
        
        #### Apply mask to IMG and fit matrices
        Aq = np.transpose(A[:,q[0],q[1]])
        IMGq = IMG[q[0],q[1]]
        
        #### Get fit parameters with least-sq. fit.
        p, resid, rank, s = scipy.linalg.lstsq(Aq,IMGq)

        print p
        
        #### Create the bg fit image from the fit parameters
        IMGout = IMG*0.
        for i in range(self.NPARAM):
            IMGout += A[i,:,:]*p[i]
        print 'Done'
        
        #### Save fit parameters to an ASCII file
        fp = open(root+'_flt.polybg','w')
        for pi in p:
            fp.write('%13.5e\n' %pi)
        fp.close()
        
        #### Show the results, note that subsequent 
        #### plots aren't cleared from memory, so memory 
        #### fills up quickly with repeated calls with show=True.
        if show:
            dshow = 0.3
            plt.figure()
            plt.subplot(221)
            plt.imshow(IMGb,vmin=np.median(IMGb)-dshow,
                vmax=np.median(IMGb)+dshow)
            plt.subplot(222)
            plt.imshow(IMG-IMGout,vmin=0-dshow,vmax=dshow)
            plt.subplot(223)
            plt.imshow(IMGout,vmin=np.median(IMGb)-dshow,
                vmax=np.median(IMGb)+dshow)

            plt.subplot(224)
            plt.imshow(DQ,vmin=0,vmax=10)
        
        if save_fit:
            hdu = pyfits.PrimaryHDU()
            save_im = pyfits.HDUList([hdu])
            save_im[0].data = IMGout
            save_im.writeto(root+'_flt.BG.fits', clobber=True)
        
        #### Subtract fitted background, write
        #### bg-subtracted image to original FLT file if `overwrite` is True
        FIX = IMG-IMGout
        if overwrite:
            print 'Overwrite: '+root
            fi[1].data = FIX
            fi.flush()
        
        #### Save images to self.
        self.FIX = FIX
        self.IMG = IMG
        self.MODEL = IMGout
        fi.close()

def asn_grism_background_subtract(asn_file='ibhj42040_asn.fits', nbin=8, path='./', verbose=True, savefig=True):
    """
    Run the 1-D background subtraction routine for all FLT files
    defined in an ASN file.
    """
    
    asn = threedhst.utils.ASNFile(asn_file)
    if verbose:
        print 'Background:'
    for flt in asn.exposures:
        if verbose:
            print '   %s' %flt
        test = oned_grism_background_subtract(flt, nbin=nbin, verbose=verbose, savefig=savefig)
            
    
def oned_grism_background_subtract(flt_root, nbin=8, path='./', savefig=True, force=False, verbose=True):
    """
    Collapse a WFC FLT image along the y-axis to get a 
    model of the overall background.  The structure of 
    the sky background seems to be primarily along this axis.
    
    Note that this assumes that the grism sky background has already 
    been subtracted from the FLT file, and that an object mask 
    exists with filename `flt_root` + '_flt.seg.fits[.gz]'.
    
    Input is a rootname, like 'ibhm46i3q'.
    
    `nbin` is the number of pixels to combine along X to 
    smooth out the profile.
    """
    from threedhst.utils import find_fits_gz
    # 
    
    #### Find fits or fits.gz files
    flt_file = find_fits_gz(flt_root+'_flt.fits', hard_break=True)
    seg_file = find_fits_gz(flt_root+'_flt.seg.fits', hard_break=True)
    
    #### Open fits files
    flt = pyfits.open(flt_file,'update')
    seg = pyfits.open(seg_file)
    
    #### Don't proceed if not a grism exposure
    keys = flt[0].header.keys()
    IS_GRISM = False
    FILTER_STRING = ""
    for key in keys:
        if key.startswith('FILTER'):
            FILTER_STRING += flt[0].header[key]+" "
            if flt[0].header[key].startswith('G'):
                IS_GRISM = True
                
    if not IS_GRISM:
        if verbose:
            print '%s is not a grism exposure (%s)' %(flt_root, FILTER_STRING)
        return False
        
    #### Don't proceed if already been done
    if 'GRIS-BG' in flt[1].header.keys():
        if (flt[1].header['GRIS-BG'] == 1) | (force is False):
            if verbose:
                print 'Background already subtracted from %s.' %(flt_root)
            return False
    
    #### Arrays     
    xi = np.arange(1014/nbin)*nbin+nbin/2.
    yi = xi*1.
    si = xi*1.
     
    #### Set up output plot  
    if savefig:
        fig = plt.figure(figsize=[5,3],dpi=100)
        fig.subplots_adjust(wspace=0.2,hspace=0.02,left=0.17,
                            bottom=0.17,right=0.97,top=0.97)
        ax = fig.add_subplot(111)
    
    #### Iterate on bg subtraction
    NITER = 4
    for it in range(NITER):
        for i in np.arange(1014/nbin):
            #### Masks, object and DQ
            seg_stripe = seg[0].data[:,i*nbin:(i+1)*nbin]
            dq_stripe = flt[3].data[:,i*nbin:(i+1)*nbin]
            OK_PIXELS = (seg_stripe == 0) & ((dq_stripe & (dq_stripe*0+4096)) == 0)
            #### Data columns
            data = flt[1].data[:,i*nbin:(i+1)*nbin]
            
            for it2 in range(1):
                #### Biweight mean and sigma
                stats = threedhst.utils.biweight(data[OK_PIXELS], both=True)
                yi[i], si[i] = stats
                OK_PIXELS = OK_PIXELS & (np.abs(data-0*stats[0]) < 3*stats[1])
                        
            # ypix, xpix = np.indices(data.shape)
            # xx = (ypix-507)/2.
            # poly = polyfit(xx[OK_PIXELS], data[OK_PIXELS], 4)
            # flt[1].data[:,i*nbin:(i+1)*nbin] -= polyval(poly, xx)
            # plt.plot(ypix[OK_PIXELS],data[OK_PIXELS],marker='.', linestyle='None', alpha=0.3)
            # plt.plot(ypix[OK_PIXELS],polyval(poly,xx[OK_PIXELS]),marker='.', alpha=0.8, linestyle='None')
            
        if savefig:
            plt.plot(xi, yi, color='red', alpha=1-it*0.8/(NITER-1))
        
        #### Interpolate smoothed back to individual pixels
        xpix = np.arange(1014)
        ypix = np.interp(xpix, xi, yi)
        for i in np.arange(1014):
            flt[1].data[:,i] -= ypix[i]
    
    #### Output figure
    if savefig:
        plt.plot(xi, yi*0, linestyle='--', alpha=0.6, color='black')
        plt.xlabel('x pixel')
        plt.ylabel('e-/s')
        plt.xlim(-1,1015)
        plt.savefig(flt_root+'_flt.residual.png')
        plt.close()
    
    #### Add a 'GRIS-BG' header keyword to the FLT[DATA] extension.
    flt[1].header.update('GRIS-BG',1)
    
    #### Dump to FITS file
    flt.flush()
    
    return True

def make_grism_shiftfiles(direct_files='ib*050_asn.fits', 
                          grism_files='ib*060_asn.fits'):
    """
make_grism_shiftfiles(direct_files='ib*050_asn.fits', 
                      grism_files='ib*060_asn.fits')
    
    Find all of the shiftfiles determined for the direct images
    and make corresponding versions for the G141 associations.
    """
    import threedhst
    import glob
    asn_direct_files = glob.glob(direct_files)
    asn_grism_files = glob.glob(grism_files)
    for i, asn_direct_file in enumerate(asn_direct_files):
        asn_grism_file=asn_grism_files[i]
        # asn_grism_file = asn_direct_file.split('50_asn')[0]+'60_asn.fits'
        threedhst.shifts.make_grism_shiftfile(asn_direct_file, asn_grism_file)
    
def prep_all(asn_files='ib*050_asn.fits', get_shift=True, bg_skip=False,
             redo_background=True, bg_only=False,
             ALIGN_IMAGE='../ACS/h_nz_sect*img.fits', ALIGN_EXT = 0,
             skip_drz=False,final_scale=0.06, pixfrac=0.8,
             IMAGES=['/research/HST/GRISM/CONF/G141_sky_cleaned.fits'],
             align_geometry='rxyscale,shift', 
             initial_order=-1,
             clean=True,
             save_fit=False):
    """
prep_all(asn_files='ib*050_asn.fits', get_shift=True, 
         redo_background=True, bg_only=False)
    
    asn_files = glob.glob(asn_files)
    
    Run prep_flt on all direct or grism associations in the current directory.
    See `prep_flt` for parameter descriptions.
    
    """
    import glob
    # asn_files = glob.glob('ib*050_asn.fits')
    # if grism:
    #     asn_files = glob.glob('ib*060_asn.fits')
    asn_files = glob.glob(asn_files)
       
    for file in asn_files:
        prep_flt(asn_file=file, get_shift=get_shift, bg_skip=bg_skip,
                    redo_background=redo_background,
                    bg_only=bg_only, ALIGN_IMAGE=ALIGN_IMAGE,
                    skip_drz=skip_drz,final_scale=final_scale, pixfrac=pixfrac,
                    IMAGES=IMAGES,
                    initial_order=initial_order,
                    align_geometry=align_geometry, clean=clean,
                    save_fit=save_fit)
                
def prep_flt(asn_file=None, get_shift=True, bg_only=False, bg_skip=False,
                first_run=True, redo_background=True,
                ALIGN_IMAGE='../ACS/h_nz_sect*img.fits', ALIGN_EXT = 0, 
                skip_drz=False, final_scale=0.06, pixfrac=0.8,
                IMAGES=['/research/HST/GRISM/CONF/G141_sky_cleaned.fits'],
                align_geometry='rxyscale,shift', clean=True,
                initial_order=-1, save_fit=False):
    """
prep_flt(asn_file=None, get_shift=True, bg_only=False,
            redo_background=True)

    
    Subtract background and align WCS of direct/grism FLT files.
    
    1) Apply the DQ masks defined in the *mask.reg files, as created
       by threedhst.dq
    
    2) First pass on background subtraction 
        
        o 0th order is median background image, e.g. G141 sky 
        
        o 1-nth order is polynomial fit with x-y cross terms.
    
        o [if `bg_only` is True then return]
        
    3) Run tweakshifts  [if `get_shift` is True & `grism` is False]
    
    4) Run Multidrizzle with first guess tweakshifts
    
    5) Get object mask for FLT files:
    
        o Blot DRZ output to all the individual FLT frames
        
        o Run SExtractor on blot images for object (segmentation)
          mask  
    
    6) Use threedhst routines to align the F140W direct image to
       ACS reference.   [if `get_shift` is True & `grism` is False]
       
    7) Redo background subtraction with improved object mask defined
       by the segmentation images [if redo_background is True]
       
    8) Subtract the collapsed background to fix residuals from the grism sky fit
    
    9) Run multidrizzle again [if redo_background is True]
    
    
    """
    #import fit_2d_poly
    import threedhst
    import os
    import glob
    
    #import threedhst.dq    
    
    if asn_file is None:
        asn_file = 'ib3728050_asn.fits'
    
    if bg_skip:
        bg_only=False
        redo_background=False
        
    ROOT_DIRECT = asn_file.split('_asn.fits')[0]
    # ALIGN_IMAGE = '../ACS/h_nz_sect*img.fits'
    
    asn = threedhst.utils.ASNFile(asn_file)
        
    #### First pass background subtraction
    if not bg_skip:
        #### Set up matrix for fitting
        fit = fit_2D_background(ORDER=initial_order,
                                IMAGES=IMAGES)#, x0=507, y0=507)
        
        for exp in asn.exposures:
            #threedhst.regions.apply_dq_mask(exp+'_flt.fits')
            fit.fit_image(exp, A=fit.A, show=False, overwrite=True,
                          save_fit=save_fit)
    
    #### Stop here if only want background subtraction
    if bg_only:
        return
        
    #### First guess at shifts
    if get_shift:
        threedhst.shifts.run_tweakshifts(asn_file, verbose=True)
        threedhst.shifts.checkShiftfile(asn_file)
        
    if not skip_drz:
        startMultidrizzle(asn_file, use_shiftfile=True, 
            skysub=bg_skip, final_scale=final_scale, pixfrac=pixfrac,
            driz_cr=first_run, median=first_run, updatewcs=first_run)
                    
    #### Blot combined images back to reference frame and make a 
    #### segmentation mask
    run = MultidrizzleRun((asn_file.split('_asn.fits')[0]).upper())
    
    #### ACS has entries in run file for each of two WFC chips
    flt = pyfits.open(asn.exposures[0]+'_flt.fits')
    inst = flt[0].header.get('INSTRUME').strip()
    if inst is 'ACS':
        skip=2
    else:
        skip=1
        
    if redo_background:
        for i,exp in enumerate(asn.exposures):
            run.blot_back(ii=i*skip, copy_new=(i is 0))
            make_segmap(run.flt[i])
    
    if get_shift:
        #### If shift routine gets confused, run the following instead
        #for geom in ['shift','rxyscale','shift']:
        # for geom in ['rxyscale','shift']:
        for geom in align_geometry.split(','):
            threedhst.shifts.refine_shifts(ROOT_DIRECT=ROOT_DIRECT,
                          ALIGN_IMAGE=ALIGN_IMAGE, ALIGN_EXTENSION = ALIGN_EXT,
                          fitgeometry=geom.strip(), clean=clean)
            
            startMultidrizzle(asn_file, use_shiftfile=True, skysub=True,
                final_scale=final_scale, pixfrac=pixfrac, driz_cr=False,
                updatewcs=False, clean=clean, median=False)
                
        
    #### Run BG subtraction with improved mask and run multidrizzle again
    if redo_background:
        fit = fit_2D_background(ORDER=initial_order, IMAGES=IMAGES)
        for exp in asn.exposures:
            #### 2-D background, fit
            fit.fit_image(exp, A=fit.A, show=False, overwrite=True, 
                          save_fit=save_fit)
            
            #### 1-D background, measured
            test = oned_grism_background_subtract(exp, nbin=26, savefig=True, verbose=False)
            
        startMultidrizzle(asn_file, use_shiftfile=True, skysub=False,
            final_scale=final_scale, pixfrac=pixfrac, driz_cr=False,
            updatewcs=False, median=False, clean=clean)
    
    if clean:
        files=glob.glob('*BLOT*')
        for file in files: 
            #print 'rm '+file
            os.remove(file)
    
def flag_data_quality():
    """ 
    Flag asteroid trails.
    """
    import threedhst.dq
    
    ####********************************************####
    ####                 AEGIS
    ####********************************************####
    os.chdir('/research/HST/GRISM/3DHST/AEGIS/PREP_FLT')
    
    threedhst.dq.checkDQ('ibhj42030_asn.fits','ibhj42040_asn.fits', 
                         path_to_flt='./')
    
    
    ####********************************************####
    ####                 COSMOS
    ####********************************************####
    os.chdir('/research/HST/GRISM/3DHST/COSMOS/PREP_FLT')

    threedhst.dq.checkDQ('ibhm31030_asn.fits','ibhm31040_asn.fits')
    threedhst.dq.checkDQ('ibhm44030_asn.fits','ibhm44040_asn.fits')
    threedhst.dq.checkDQ('ibhm51030_asn.fits','ibhm51040_asn.fits')
    threedhst.dq.checkDQ('ibhm53030_asn.fits','ibhm53040_asn.fits')
    
    ####********************************************####
    ####                 SN-MARSHALL
    ####********************************************####
    os.chdir('/research/HST/GRISM/3DHST/SN-MARSHALL/PREP_FLT')
    
    threedhst.dq.checkDQ('ibfuw1070_asn.fits','ibfuw1070_asn.fits', 
                         path_to_flt='./')
    
def process_all():
    """
    Initial processing of all 3D-HST frames
    """
    from threedhst.prep_flt_files import process_3dhst_pair as pair
    
    ####********************************************####
    ####                 GOODS-N
    ####********************************************####
    os.chdir('/research/HST/GRISM/3DHST/GOODS-N/PREP_FLT')
    ALIGN = '../ACS/h_nz*drz_img.fits'
    
    direct = glob.glob('ib*050_asn.fits')
    grism = glob.glob('ib*060_asn.fits')
    
    for i  in range(28):
        pair(direct[i], grism[i], ALIGN_IMAGE = ALIGN, SKIP_GRISM=True,
             GET_SHIFT=False, DIRECT_HIGHER_ORDER=2)
         
    #### GOODS-N-22-G_drz.fits has a bad 1-D subtraction
    
    # Make mosaic
    direct_files = glob.glob('GOODS-N*-D_asn.fits')
    threedhst.utils.combine_asn_shifts(direct_files, out_root='GOODS-N',
                       path_to_FLT='./', run_multidrizzle=False)
    
    SCALE = 0.06
    NX = np.int(0.25/SCALE*2326)
    NY = np.int(0.25/SCALE*3919)
    threedhst.prep_flt_files.startMultidrizzle('GOODS-N_asn.fits',
             use_shiftfile=True, skysub=False,
             final_scale=SCALE, pixfrac=0.8, driz_cr=False,
             updatewcs=False, clean=True, median=False,
             ra=189.22805, dec=62.236116,
             final_outnx=NX, final_outny=NY, final_rot=45)
    
    #### Make direct image for each pointing that also include 
    #### neighboring pointings
    files = glob.glob('GOODS-N-*D_asn.fits')
    for file in files:
        pointing = file.split('_asn.fits')[0]
        threedhst.prep_flt_files.mosaic_to_pointing(mosaic_list='GOODS-N-*D',
                                    pointing=pointing,
                                    run_multidrizzle=True)
                                                                    
    ####********************************************####
    ####                      AEGIS
    ####********************************************####
    os.chdir('/research/HST/GRISM/3DHST/AEGIS/PREP_FLT')
    ALIGN = '../NMBS/AEGIS-N2_K_sci.fits'
    
    pair('ibhj42030_asn.fits','ibhj42040_asn.fits', ALIGN_IMAGE = ALIGN)
    pair('ibhj43030_asn.fits','ibhj43040_asn.fits', ALIGN_IMAGE = ALIGN)
    
    ### Make mosaic
    direct_files = glob.glob('AEGIS*-D_asn.fits')
    threedhst.utils.combine_asn_shifts(direct_files, out_root='AEGIS-D',
                       path_to_FLT='./', run_multidrizzle=False)
    threedhst.prep_flt_files.startMultidrizzle('AEGIS-D_asn.fits',
             use_shiftfile=True, skysub=True,
             final_scale=0.06, pixfrac=0.8, driz_cr=False,
             updatewcs=False, clean=True, median=False)
    
    threedhst.prep_flt_files.make_grism_subsets(root='AEGIS')
    
    ####********************************************####
    ####                     COSMOS
    ####********************************************####
    os.chdir('/research/HST/GRISM/3DHST/COSMOS/PREP_FLT')
    ALIGN = '../NMBS/COSMOS-1.V4.K_nosky.fits'
    SKIP = True
    pair('ibhm51030_asn.fits','ibhm51040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm31030_asn.fits','ibhm31040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm46030_asn.fits','ibhm46040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm29030_asn.fits','ibhm29040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    
    pair('ibhm43030_asn.fits','ibhm43040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm44030_asn.fits','ibhm44040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm45030_asn.fits','ibhm45040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm48030_asn.fits','ibhm48040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm52030_asn.fits','ibhm52040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm53030_asn.fits','ibhm53040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP, GRISM_HIGHER_ORDER=-1)
    pair('ibhm54030_asn.fits','ibhm54040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm55030_asn.fits','ibhm55040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    pair('ibhm56030_asn.fits','ibhm56040_asn.fits', ALIGN_IMAGE = ALIGN, IMAGES=['G141_fixed_sky.fits'], SKIP_DIRECT=SKIP)
    
    ### Make mosaic
    direct_files = glob.glob('COSMOS-*-D_asn.fits')
    threedhst.utils.combine_asn_shifts(direct_files, out_root='COSMOS-D',
                       path_to_FLT='./', run_multidrizzle=False)
    threedhst.prep_flt_files.startMultidrizzle('COSMOS-D_asn.fits',
             use_shiftfile=True, skysub=False,
             final_scale=0.06, pixfrac=0.8, driz_cr=False,
             updatewcs=False, clean=True, median=False,
             ra=150.12356, dec=2.3608425,
             final_outnx = 9355, final_outny=11501)
             
    threedhst.prep_flt_files.make_grism_subsets(root='COSMOS')
    
    ####********************************************####
    ####                   GOODS-S
    ####********************************************####
    os.chdir('/research/HST/GRISM/3DHST/GOODS-S/PREP_FLT')
    ALIGN = '../ECDFS/MUSYC_ECDFS_BVR.fits'
    
    pair('ibhj06030_asn.fits','ibhj06040_asn.fits', ALIGN_IMAGE = ALIGN)
    pair('ibhj27030_asn.fits','ibhj27040_asn.fits', ALIGN_IMAGE = ALIGN)
    pair('ibhj28030_asn.fits','ibhj28040_asn.fits', ALIGN_IMAGE = ALIGN)
    
    ### Make mosaic
    direct_files = glob.glob('GOODS-S*-D_asn.fits')
    threedhst.utils.combine_asn_shifts(direct_files, out_root='GOODS-S-D',
                       path_to_FLT='./', run_multidrizzle=False)
    threedhst.prep_flt_files.startMultidrizzle('GOODS-S-D_asn.fits',
             use_shiftfile=True, skysub=False,
             final_scale=0.06, pixfrac=0.8, driz_cr=False,
             updatewcs=False, clean=True, median=False,
             ra=53.24009, dec=-27.84430, 
             final_outnx=7440, final_outny=4650)
    
    threedhst.prep_flt_files.make_grism_subsets(root='GOODS-S')
    
    ####********************************************####
    ####              SN-MARSHALL (UDS)
    ####********************************************####
    ## Shifts and direct images determined separately
    os.chdir('/research/HST/GRISM/3DHST/SN-MARSHALL/PREP_FLT')
    #### First orientation
    for i in range(3,6,2):
        ist = '%0d' %(i)
        shutil.copy('ibfuw'+ist+'070_shifts.txt',
                    'MARSHALL'+ist+'-G_shifts.txt')
        pair(None,'ibfuw'+ist+'070_asn.fits', ALIGN_IMAGE = None, SKIP_DIRECT=True, SKIP_GRISM=False, GRISM_HIGHER_ORDER=2)
    
    asn_list = glob.glob('MARSHALL[135]-G_asn.fits')
    threedhst.utils.combine_asn_shifts(asn_list, out_root='MARSHALLa',
                    path_to_FLT='./', run_multidrizzle=False)
    threedhst.prep_flt_files.startMultidrizzle('MARSHALLa_asn.fits',
                 use_shiftfile=True, skysub=False,
                 final_scale=0.06, pixfrac=0.8, driz_cr=False,
                 updatewcs=False, clean=True, median=False)
    
    #### Second orientation
    for i in range(2,7,2):
        ist = '%0d' %(i)
        shutil.copy('ibfuw'+ist+'070_shifts.txt',
                    'MARSHALL'+ist+'-G_shifts.txt')
        pair(None,'ibfuw'+ist+'070_asn.fits', ALIGN_IMAGE = None, SKIP_DIRECT=True, SKIP_GRISM=False, GRISM_HIGHER_ORDER=2)
    
    asn_list = glob.glob('MARSHALL[246]-G_asn.fits')
    threedhst.utils.combine_asn_shifts(asn_list, out_root='MARSHALLb',
                    path_to_FLT='./', run_multidrizzle=False)
    threedhst.prep_flt_files.startMultidrizzle('MARSHALLb_asn.fits',
                 use_shiftfile=True, skysub=False,
                 final_scale=0.06, pixfrac=0.8, driz_cr=False,
                 updatewcs=False, clean=True, median=False)
    
    ####********************************************####
    ####         SN-PRIMO (GOODS-S, UDF)
    ####********************************************####
    ## Shifts and direct images determined separately
    os.chdir('/research/HST/GRISM/3DHST/SN-PRIMO/PREP_FLT')
    shutil.copy('G141_1026_shifts.txt','PRIMO-1026-G_shifts.txt')
    pair(None,'G141_1026_asn.fits', ALIGN_IMAGE = None, SKIP_DIRECT=True, SKIP_GRISM=False, IMAGES=['G141_fixed_sky.fits'], GRISM_HIGHER_ORDER=2)
    
    shutil.copy('G141_1101_shifts.txt','PRIMO-1101-G_shifts.txt')
    pair(None,'G141_1101_asn.fits', ALIGN_IMAGE = None, SKIP_DIRECT=True, SKIP_GRISM=False, IMAGES=['G141_fixed_sky.fits'], GRISM_HIGHER_ORDER=2)
    
    ####********************************************####
    ####            SN-GEORGE (GOODS-S)
    ####********************************************####
    ## Shifts and direct images determined separately
    shutil.copy('ibfug1040_shifts.txt','GEORGE-1-G_shifts.txt')
    pair(None,'ibfug1040_asn.fits', ALIGN_IMAGE = None, SKIP_DIRECT=True, SKIP_GRISM=False,  GRISM_HIGHER_ORDER=2)

    shutil.copy('ibfug2040_shifts.txt','GEORGE-2-G_shifts.txt')
    pair(None,'ibfug2040_asn.fits', ALIGN_IMAGE = None, SKIP_DIRECT=True, SKIP_GRISM=False,  GRISM_HIGHER_ORDER=2)
    
    asn_files = glob.glob('GEORGE-?-G_asn.fits')
    threedhst.utils.combine_asn_shifts(asn_files, out_root='GEORGE',
                    path_to_FLT='./', run_multidrizzle=False)
    
    threedhst.prep_flt_files.startMultidrizzle('GEORGE_asn.fits',
                 use_shiftfile=True, skysub=False,
                 final_scale=0.06, pixfrac=0.8, driz_cr=False,
                 updatewcs=False, clean=True, median=False)
    
    
def mosaic_to_pointing(mosaic_list='GOODS-N-*D',
                       pointing='GOODS-N-43-D',
                       run_multidrizzle=True):
    """ 
    Given an input list of ASN tables that could be combined to form a mosaic,
    find only those that overlap with the input pointing and make a new ASN
    table for that pointing that includes the other overlapping pointings.
    
    This would be identical to using the full mosaic ASN and then using the 
    individual pointing as a reference.  The only difference is that this 
    procedure only includes images that overlap with the output file to 
    save computation time.
    """
    from threedhst.shifts import find_align_images_that_overlap as overlap
     
    list = overlap(pointing+'_drz.fits', mosaic_list+'*drz.fits',
                   ALIGN_EXTENSION=1)
    
    asn_files = []
    for item in list:
        asn_files.append(item.replace('drz','asn'))
    
    threedhst.utils.combine_asn_shifts(asn_files, out_root='mostmp',
                    path_to_FLT='./', run_multidrizzle=False)
    
    threedhst.prep_flt_files.startMultidrizzle('mostmp_asn.fits',
                 use_shiftfile=True, skysub=False,
                 final_scale=0.06, pixfrac=0.8, driz_cr=False,
                 updatewcs=False, clean=True, median=False,
                 refimage=pointing+'_drz.fits[1]')
    
    os.remove('mostmp_shifts.txt')
    os.remove('mostmp_asn.fits')
    #### Copy the outputs but not the ASN or shift files
    files=glob.glob('mostmp*')
    for file in files:
        out=file.replace('mostmp',pointing)
        shutil.move(file, out)
        
    
def make_grism_subsets(root='GOODS-S', run_multidrizzle=True, single=None):
    """
    Group grism exposures with the same orientation angle together.  
    
    Use the full direct mosaic (root+'-D_drz.fits') as a MDRZ reference
    image if it exists.
    
    """
    import threedhst.catIO as catIO
    
    info = catIO.Readfile('files.info')
    
    if root=='GOODS-S':
        for i in range(info.N):
            info.targname[i] = info.targname[i].replace('SOUTH','S')
    
    if root=='GOODS-N':
        for i in range(info.N):
            info.targname[i] = info.targname[i].replace('GNGRISM','GOODS-N-')
        
    info.pa_v3 = np.cast[int](np.round(info.pa_v3))
    angles = np.unique(np.round(info.pa_v3))
    
    #### Just redo one angle
    if single is not None:
        angles = [angles[single]]
    
    for angle in angles:
        targets = np.unique(info.targname[info.pa_v3 == angle])
        list = []
        for targ in targets:
            list.append("%s-G_asn.fits" %(targ))
        #
        out_root = root+'-%03d' %(angle)
        print out_root
        
        threedhst.utils.combine_asn_shifts(list, out_root=out_root,
                   path_to_FLT='./', run_multidrizzle=False)
        #
        if run_multidrizzle:
            direct_ref = root+'-D_drz.fits'
            if not os.path.exists(direct_ref):
                direct_ref=''
            else:
                direct_ref+='[1]'
            
            threedhst.prep_flt_files.startMultidrizzle(out_root+'_asn.fits',
                 use_shiftfile=True, skysub=False,
                 final_scale=0.06, pixfrac=0.8, driz_cr=False,
                 updatewcs=False, clean=True, median=False,
                 refimage=direct_ref)
                 
        
def make_targname_asn(asn_file, newfile=True):
    """
    Take an ASN file like 'ibhm51030_asn.fits' and turn it into 
    'COSMOS-3-D_asn.fits'
    """
    asn = threedhst.utils.ASNFile(asn_file)
    
    im = pyfits.open('../RAW/'+asn.exposures[0]+'_flt.fits.gz')
    filter = im[0].header['FILTER']
    
    if filter.startswith('F'):
        type='D'
    else:
        type='G'
    
    target = im[0].header['TARGNAME']
    target = target.replace('SOUTH','S')
    target = target.replace('GNGRISM','GOODS-N-')
    target = target.replace('GEORGE','GEORGE-')
    
    if target == 'MARSHALL':
        #### Add the pointing number, 1-6
        ID = asn.exposures[0][5]
        target+=ID

    if target == 'PRIMO':
        #### Add the date, like "1026"
        date = ''.join(im[0].header['DATE-OBS'].split('-')[1:])
        target+='-'+date
    
    if target.startswith('GEORGE'):
        #### Add the date, like "1026"
        hour = np.int(im[0].header['TIME-OBS'].split(':')[0])
        if hour > 12:
            target='GEORGE-2'
        else:
            target='GEORGE-1'
            
    
    product = target+'-'+type
    asn.product = product
    if newfile:
        asn.write(product+'_asn.fits', clobber=True)
    return product+'_asn.fits'
    
def process_3dhst_pair(asn_direct_file='ib3706050_asn.fits',
                       asn_grism_file='ib3706060_asn.fits',
                       ALIGN_IMAGE='../ACS/h_nz_sect*img.fits',
                       PATH_TO_RAW='../RAW',
            IMAGES = ['/research/HST/GRISM/CONF/G141_sky_cleaned.fits',
                      '/research/HST/GRISM/CONF/G141wLO_fixed_sky.fits', 
                      '/research/HST/GRISM/CONF/G141wHI_fixed_sky.fits'],
                       SKIP_GRISM=False,
                       SKIP_DIRECT=False,
                       GET_SHIFT=True,
                       DIRECT_HIGHER_ORDER=2,
                       GRISM_HIGHER_ORDER=1):
    
    import threedhst
    import threedhst.prep_flt_files
    from threedhst.prep_flt_files import make_targname_asn
    
    if asn_direct_file:
        asn_direct_file = make_targname_asn(asn_direct_file)
    
    if asn_grism_file:
        asn_grism_file = make_targname_asn(asn_grism_file)
    print 'DIRECT: %s, GRISM: %s\n' %(asn_direct_file, asn_grism_file)
        
    ##### Direct images
    if not SKIP_DIRECT:
        
        threedhst.process_grism.fresh_flt_files(asn_direct_file,
                      from_path=PATH_TO_RAW)

        ##### Make region files for the pointing
        if not os.path.exists(asn_direct_file.replace('fits','pointing.reg')):
            threedhst.regions.asn_region(asn_direct_file)
        
        threedhst.prep_flt_files.prep_flt(asn_file=asn_direct_file,
                        get_shift=GET_SHIFT, 
                        bg_only=False, bg_skip=False, redo_background=True,
                        ALIGN_IMAGE=ALIGN_IMAGE,
                        skip_drz=False, final_scale=0.06, pixfrac=0.8,
                        IMAGES=[],
                        align_geometry='rxyscale,shift', clean=True,
                        initial_order=0, save_fit=False)
        
        if DIRECT_HIGHER_ORDER > 0:
            threedhst.prep_flt_files.prep_flt(asn_file=asn_direct_file,
                        get_shift=False, 
                        bg_only=False, bg_skip=False, redo_background=False,
                        skip_drz=False, final_scale=0.06, pixfrac=0.8,
                        IMAGES=[], clean=True,
                        initial_order=DIRECT_HIGHER_ORDER, save_fit=False)
    
    #### Grism images
    if not SKIP_GRISM:
        if asn_direct_file:
            threedhst.shifts.make_grism_shiftfile(asn_direct_file,
                                                  asn_grism_file)

        threedhst.process_grism.fresh_flt_files(asn_grism_file,
                      from_path=PATH_TO_RAW)
        
        if not os.path.exists(asn_grism_file.replace('fits','pointing.reg')):
            threedhst.regions.asn_region(asn_grism_file)
        
        threedhst.prep_flt_files.prep_flt(asn_file=asn_grism_file,
                        get_shift=False, 
                        bg_only=False, bg_skip=False, redo_background=True,
                        skip_drz=False, final_scale=0.06, pixfrac=0.8,
                        IMAGES=IMAGES, clean=True,
                        initial_order=-1, save_fit=False)

        if GRISM_HIGHER_ORDER > 0:
            threedhst.prep_flt_files.prep_flt(asn_file=asn_grism_file,
                        get_shift=False, first_run=False,
                        bg_only=False, bg_skip=False, redo_background=False,
                        skip_drz=False, final_scale=0.06, pixfrac=0.8,
                        IMAGES=[], clean=True,
                        initial_order=GRISM_HIGHER_ORDER, save_fit=False)
    

def startMultidrizzle(root='ib3727050_asn.fits', use_shiftfile = True,
    skysub=True, updatewcs=True, driz_cr=True, median=True,
    final_scale=0.06, pixfrac=0.8, clean=True,
    final_outnx='', final_outny='', final_rot=0., ra='', dec='', 
    refimage='', unlearn=True):
    """
startMultidrizzle(root='ib3727050_asn.fits', use_shiftfile = True,
                  skysub=True, final_scale=0.06, updatewcs=True, driz_cr=True,
                  median=True, final_scale=0.06, pixfrac=0.8, 
                  final_outnx='', final_outny='', final_rot=0., ra='', dec='',
                  refimage='', unlearn=True)
    
    Run multidrizzle on an input asn table.
    
    if `use_shiftfile` is True:
        use a root+'_shifts.txt' shiftfile.
    else: 
        no shiftfile
    
    if skysub is True:
        Run multidrizzle WITH sky subtraction
    else:
        "        "       WITHOUT   "         "
        
    final_scale: Final pixel scale of output image (arcsec)
    
    """
    asn_direct_file = root #'ib3727050_asn.fits'
    
    asn_direct = threedhst.utils.ASNFile(file=asn_direct_file)
    ROOT_DIRECT = asn_direct_file.split('_asn')[0]
    
    if use_shiftfile:
        shiftfile=ROOT_DIRECT+'_shifts.txt'
    else:
        shiftfile=''
    
    #### If fewer than 4 exposures in the asn list, use
    #### a larger `pixfrac`.
    if len(asn_direct.exposures) < 4:
        pixfrac = 1.0
    
    if skysub:
        skysub=yes
    else:
        skysub=no
    
    if updatewcs:
        updatewcs=yes
    else:
        updatewcs=no
    
    if driz_cr:
        driz_cr=yes
    else:
        driz_cr=no

    if median:
        median=yes
    else:
        median=no
    
    if unlearn:
        iraf.unlearn('multidrizzle')
        
    #### Run Multidrizzle
    iraf.multidrizzle(input=asn_direct_file, \
       shiftfile=shiftfile, \
       output = '', skysub = skysub, updatewcs = updatewcs, driz_cr=driz_cr,
       final_scale = final_scale, final_pixfrac = pixfrac, median=median, 
       blot=median, driz_separate=median, static=median,
       driz_sep_outnx = final_outnx, driz_sep_outny = final_outny, 
       final_outnx=final_outnx, final_outny=final_outny, 
       final_rot=final_rot, ra=ra, dec=dec, refimage=refimage)
    
    #### Delete created files    
    if clean is True:
        threedhst.process_grism.cleanMultidrizzleOutput()
            
class MultidrizzleRun():
    """
MultidrizzleRun(root='IB3728050')
    
    Read a .run file output from MultiDrizzle.
    
    Get list of flt files and their shifts as used by multidrizzle.
    """
    def __init__(self, root='IB3728050'):
        import numpy as np
        
        runfile = root+'.run'
        self.root = root
        
        self.flt = []
        self.xsh = []
        self.ysh = []
        self.rot = []
        self.scl = 1.
        self.exptime = []
        
        for line in open(runfile,'r'):
            if line.startswith('drizzle.scale'):
                self.scl = line.split()[2]
            if line.startswith('drizzle '):
                spl = line.split()
                self.flt.append(spl[1].split('.fits')[0])
                self.exptime.append(-1)
                for tag in spl:
                    if tag.startswith('xsh'):
                        self.xsh.append(np.float(tag.split('=')[1]))
                    if tag.startswith('ysh'):
                        self.ysh.append(np.float(tag.split('=')[1]))
                    if tag.startswith('rot'):
                        self.rot.append(np.float(tag.split('=')[1]))
        
        self.count = len(self.flt)
        
    def blot_back(self, ii=0, SCI=True, WHT=True, copy_new=True, shape = None):
        """
blot_back(self, ii=0, SCI=True, WHT=True, copy_new=True)
    
    Blot the output DRZ file back to exposure #ii pixels.
    
    if SCI is True:
        blot science extension to FLT+'.BLOT.SCI.fits'

    if WHT is True:
        blot weight extension to FLT+'.BLOT.WHT.fits'
    
    if copy_new is True:
        imcopy SCI and WHT extensions of DRZ image to separate files.
        
        """
        #flt_orig = pyfits.open('../RAW/'+self.flt[ii]+'.fits.gz')
        threedhst.process_grism.flprMulti()
        
        if self.exptime[ii] < 0:
            try:
                flt_orig = pyfits.open(self.flt[ii]+'.fits')
                exptime = flt_orig[0].header.get('EXPTIME')
                filter = flt_orig[0].header.get('FILTER').strip()
                flt_orig.close()
            except:
                exptime = 1.
                filter='INDEF'
        else:
            exptime = self.exptime[ii]
        
        if shape is None:  
            try:
                inNX = flt_orig[1].header.get('NAXIS1')
                inNY = flt_orig[1].header.get('NAXIS2')
            except:
                inNX = 1014
                inNY = 1014
            
            shape = (inNX, inNY)
        else:
            inNX, inNY = shape
        
        #### Need to update reference position of coeffs file
        #### for an output shape different than 1014, 1014
        coeffs = self.flt[ii]+'_coeffs1.dat'
        coeffs_lines = open(coeffs).readlines()
        if shape != (1014, 1014):
            for i, line in enumerate(coeffs_lines):
                if line.strip().startswith('refpix'):
                    ### Default to center pixel
                    coeffs_lines[i] = 'refpix %9.3f %9.3f\n' %(inNX*1./2, inNY*1./2)
        
        fp = open('/tmp/coeffs1.dat','w')
        fp.writelines(coeffs_lines)
        fp.close()
        
                
        iraf.delete(self.flt[ii]+'.BLOT.*.fits')
        if copy_new:
            iraf.delete('drz_*.fits')
            # iraf.imcopy(self.root+'_drz.fits[1]','drz_sci.fits')
            # iraf.imcopy(self.root+'_drz.fits[2]','drz_wht.fits')
            
            ### NEED TO STRIP FITS HEADER
            im_drz = pyfits.open(self.root+'_drz.fits')
            sci = im_drz[1].data            
            s_hdu = pyfits.PrimaryHDU(sci)
            s_list = pyfits.HDUList([s_hdu])
            copy_keys = ['CTYPE1','CTYPE2','CRVAL1','CRVAL2','CRPIX1','CRPIX2','CD1_1','CD1_2','CD2_1','CD2_2','LTM1_1','LTM2_2']
            s_list[0].header.update('EXPTIME',im_drz[0].header.get('EXPTIME'))
            s_list[0].header.update('CDELT1',im_drz[1].header.get('CD1_1'))
            s_list[0].header.update('CDELT2',im_drz[1].header.get('CD2_2'))
            for key in copy_keys:
                s_list[0].header.update(key, im_drz[1].header.get(key))
            s_list.writeto('drz_sci.fits', clobber=True)
            
            wht = im_drz[2].data
            w_hdu = pyfits.PrimaryHDU(wht)
            w_list = pyfits.HDUList([w_hdu])
            copy_keys = ['CTYPE1','CTYPE2','CRVAL1','CRVAL2','CRPIX1','CRPIX2','CD1_1','CD1_2','CD2_1','CD2_2','LTM1_1','LTM2_2']
            w_list[0].header.update('EXPTIME',im_drz[0].header.get('EXPTIME'))
            w_list[0].header.update('CDELT1',im_drz[1].header.get('CD1_1'))
            w_list[0].header.update('CDELT2',im_drz[1].header.get('CD2_2'))
            for key in copy_keys:
                w_list[0].header.update(key, im_drz[1].header.get(key))
            w_list.writeto('drz_wht.fits', clobber=True)
            
        if SCI:
            iraf.blot(data='drz_sci.fits',
                outdata=self.flt[ii]+'.BLOT.SCI.fits', scale=self.scl,
                coeffs='/tmp/coeffs1.dat', xsh=self.xsh[ii], 
                ysh=self.ysh[ii], 
                rot=self.rot[ii], outnx=inNX, outny=inNY, align='center', 
                shft_un='input', shft_fr='input', in_un='cps', out_un='cps', 
                interpol='poly5', sinscl='1.0', expout=exptime, 
                expkey='EXPTIME',fillval=0.0)
        
        if WHT:
            iraf.blot(data='drz_wht.fits',
                outdata=self.flt[ii]+'.BLOT.WHT.fits', scale=self.scl,
                coeffs='/tmp/coeffs1.dat', xsh=self.xsh[ii], 
                ysh=self.ysh[ii], 
                rot=self.rot[ii], outnx=inNX, outny=inNY, align='center', 
                shft_un='input', shft_fr='input', in_un='cps', out_un='cps', 
                interpol='poly5', sinscl='1.0', expout=exptime, 
                expkey='EXPTIME',fillval=0.0)
        
        iraf.delete('drz_*.fits')
        
class DRZFile(MultidrizzleRun):
    """
    Get the information from a drz file directly, rather than from a .run 
    file
    """
    def __init__(self, fitsfile='ib6o23010_drz.fits'):
        import numpy as np
        
        self.root = fitsfile.split('_drz.fits')[0]
        
        drz = pyfits.open(fitsfile)
        hdrz = drz[0].header
        self.count = 0
        for key in hdrz.keys():
            if key.startswith('D') & key.endswith('XSH'):
                self.count+=1
        
        self.flt = []
        self.xsh = []
        self.ysh = []
        self.rot = []
        self.scl = 0.
        self.exptime = []
        
        for i in range(self.count):
            self.flt.append(hdrz.get('D%03dDATA' %(i+1)).split('.fits')[0])
            self.xsh.append(hdrz.get('D%03dXSH' %(i+1)))
            self.ysh.append(hdrz.get('D%03dYSH' %(i+1)))
            self.rot.append(hdrz.get('D%03dROT' %(i+1)))
            self.exptime.append(hdrz.get('D%03dDEXP' %(i+1)))
            self.scl += hdrz.get('D%03dSCAL' %(i+1))
        
        self.scl /= self.count
        
def jitter_info():
    """
jitter_info()
    
    Get LIMBANG values from jitter files and also get 
    image stats from FLT images.  Useful for flagging 
    exposures affected by earthglow.
    """
    import glob
    import os
    import numpy as np
    
    fp = open('jitter_info.dat','w')
    
    jit_files = glob.glob('../JITTER/*jit.fits')
    jit_files = glob.glob('../RAW/ib*0_asn.fits')
    
    for file in jit_files:
        #im = pyfits.open(file)
        im = pyfits.open('../JITTER/'+ 
                         os.path.basename(file).split('_')[0]+
                         '_jit.fits')
        nExten = len(im)-1
        for ext in range(1,nExten+1):
            head = im[ext].header
            dat = im[ext].data
            flt = pyfits.open('../RAW/'+head.get('EXPNAME')[:-1]+
                              'q_flt.fits.gz')
            
            med = np.median(flt[1].data[180:300,180:300])
            
            str = ("%s %10s %8.1f %5.1f %2d %5.1f %2d  %8.2f"
               %(head.get('EXPNAME')[:-1]+'q',
               flt[0].header['FILTER'], flt[0].header['EXPTIME'],
               dat.field('LimbAng')[0], dat.field('BrightLimb')[0],
               dat.field('LimbAng')[-1], dat.field('BrightLimb')[-1],med))
            
            fp.write(str+'\n')
            print str
    
    fp.close()


# def go_make_segmap():
#     
#     import glob
#     
#     files = glob.glob('*BLOT.SCI.fits')
#     for file in files:
#         make_segmap(root=file.split('.BLOT')[0])
        
def make_segmap(root='ib3701ryq_flt', sigma=0.5):
    """
make_segmap(root='ib3701ryq_flt', sigma=1)
    
    Get a segmentation image for a flt file after creating its 
    BLOT SCI and WHT images.
    
    DETECT_THRESH = ANALYSIS_THRESH = sigma
    """
    import threedhst
    
    ## Find if image is for grism or direct image
    flt = pyfits.open(root+'.fits')
    IS_GRISM = flt[0].header.get('FILTER').startswith('G')
    flt.close()
    
    se = threedhst.sex.SExtractor()
    ## Set the output parameters required for aXe 
    ## (stored in [threedhst source]/data/aXe.param) 
    se.aXeParams()
    ## XXX add test for user-defined .conv file
    se.copyConvFile(grism=IS_GRISM)
    
    se.overwrite = True
    se.options['CATALOG_NAME']    = root+'.BLOT.SCI.cat'
    se.options['CHECKIMAGE_NAME'] = root+'.seg.fits, bg.fits'
    se.options['CHECKIMAGE_TYPE'] = 'SEGMENTATION, BACKGROUND'
    se.options['WEIGHT_TYPE']     = 'MAP_WEIGHT'
    se.options['WEIGHT_IMAGE']    = root+'.BLOT.WHT.fits'
    se.options['FILTER']    = 'Y'

    se.options['BACK_TYPE']     = 'AUTO'
    se.options['BACK_FILTERSIZE']     = '2'
    
    if IS_GRISM:
        se.options['FILTER_NAME'] = 'grism.conv'
    else:
        se.options['FILTER_NAME'] = 'default.conv'
    
    #### Detect thresholds (default = 1.5)
    se.options['DETECT_THRESH']    = '%f' %sigma
    se.options['ANALYSIS_THRESH']  = '%f' %sigma
    se.options['MAG_ZEROPOINT'] = '26.46'
    status = se.sextractImage(root+'.BLOT.SCI.fits')
    
    if os.path.exists(root+'.seg.fits.mask.reg'):
        threedhst.regions.apply_dq_mask(root+'.seg.fits', extension=0,
           addval=100)
           
def apply_best_flat(fits_file, verbose=False):
    """
    Check that the flat used in the pipeline calibration is the 
    best available.  If not, multiply by the flat used and divide
    by the better flat.
    
    Input fits_file can either be an ASN list or an individuatl FLT file
     """
    fits_list = [fits_file]
    
    if fits_file.find('_asn.fits') > 0:
        asn = threedhst.utils.ASNFile(fits_file)
        fits_list = []
        for exp in asn.exposures:
            fits_list.append(exp+'_flt.fits')
    
    for file in fits_list:
        im = pyfits.open(file, 'update')
        USED_PFL = im[0].header['PFLTFILE'].split('$')[1]
        BEST_PFL = find_best_flat(file, verbose=False)
        
        IREF = os.path.dirname(BEST_PFL)+'/'
        BEST_PFL = os.path.basename(BEST_PFL)
        
        MSG = 'PFLAT, %s: Used= %s, Best= %s' %(file, USED_PFL, BEST_PFL)
        
        if USED_PFL != BEST_PFL:
            MSG += ' *'
            used = pyfits.open(IREF+USED_PFL)
            best = pyfits.open(IREF+BEST_PFL)
            
            im[1].data *= (used[1].data/best[1].data)[5:-5,5:-5]
            im[0].header.update('PFLTFILE', 'iref$'+BEST_PFL)
            im.flush()
            
        if verbose:
            print MSG
            
def find_best_flat(flt_fits, verbose=True): #, IREF='/research/HST/GRISM/IREF/'):
    """
    Find the most recent PFL file in $IREF for the filter used for the 
    provided FLT image.  Doesn't do any special check on USEAFTER date, just
    looks for the most-recently modified file. 
    """
    import glob
    import os.path
    import time
    
    IREF = os.environ["iref"]+"/"
    
    the_filter = pyfits.getheader(flt_fits,0).get('FILTER')
    
    pfls = glob.glob(IREF+'/*pfl.fits')
    latest = 0
    best_pfl = None
    
    for pfl in pfls:
        head = pyfits.getheader(pfl)
        if head.get('FILTER') != the_filter:
            continue    
        
        this_created = os.path.getmtime(pfl)
        if this_created > latest:
            best_pfl = pfl
            latest = this_created
            
        if verbose:
            print '%s %s %s' %(pfl, the_filter, time.ctime(latest))
    
    return best_pfl #, the_filter, time.ctime(latest)
    
    